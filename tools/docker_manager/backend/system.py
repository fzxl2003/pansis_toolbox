from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})
from .images import _calc_user_image_usage
from .containers import _calc_user_container_usage
from .volumes import _calc_user_volume_usage

# ==============================================================
# CUDA 重新扫描
# ==============================================================

def rescan_server_cuda(server_id: str, user: User) -> dict[str, Any]:
    """重新扫描服务器的 CUDA/GPU 情况并更新数据库（管理员专用）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以重新扫描服务器 CUDA", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
    client = _ssh_connect(row)
    cuda_available = False
    gpu_count = 0
    gpu_info: list[dict[str, Any]] = []
    try:
        cuda_out, _, cuda_rc = _ssh_exec(
            client,
            "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null",
            timeout=20,
        )
        if cuda_rc == 0 and cuda_out.strip():
            cuda_available = True
            for line in cuda_out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_info.append({
                        "index": int(parts[0]) if parts[0].isdigit() else len(gpu_info),
                        "name": parts[1],
                        "memoryTotal": parts[2],
                    })
            gpu_count = len(gpu_info)
    finally:
        client.close()
    with get_connection() as conn:
        conn.execute(
            "UPDATE docker_servers SET cuda_available=?, gpu_count=?, gpu_info=?, updated_at=? WHERE id=?",
            (1 if cuda_available else 0, gpu_count, json.dumps(gpu_info), _now(), server_id),
        )
        row = _get_server_row(conn, server_id)
    return _public_server(row)


# ==============================================================
# 服务器资源概览（用户侧：卷配额、路径磁盘、CUDA 权限）
# ==============================================================

def get_server_resource_overview(server_id: str, user: User) -> dict[str, Any]:
    """
    获取当前用户在指定服务器上的资源使用概览：
    - 卷配额（总量 / 已用 / 剩余；按配额占用者均分计算）
    - 挂载路径的磁盘空间（总量 / 剩余 / 自己已用 / 他人已用）
    - 用户可用的 CUDA GPU 列表
    - 镜像配额（总量 / 已用 / 剩余；按配额占用者均分计算）
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        srv_row = _get_server_row(conn, server_id)
        perms = _get_user_perms(conn, server_id, user)

        # ---------- 卷配额（配额占用者均分） ----------
        vol_usage = _calc_user_volume_usage(conn, srv_row, user)

        # ---------- 挂载路径磁盘空间 ----------
        path_whitelist: list[str] = perms.get("ctr_path_whitelist", [])
        paths_info: list[dict[str, Any]] = []
        if path_whitelist:
            client = _ssh_connect(srv_row)
            try:
                for path in path_whitelist:
                    safe_path = shlex.quote(path)
                    # df: total / used / available（1K blocks → GB）
                    out, _, rc = _ssh_exec(
                        client,
                        f"df -k {safe_path} 2>/dev/null | tail -1",
                        timeout=10,
                    )
                    disk_total_gb: float | None = None
                    disk_avail_gb: float | None = None
                    disk_used_gb: float | None = None
                    if rc == 0 and out.strip():
                        cols = out.strip().split()
                        if len(cols) >= 5:
                            try:
                                disk_total_gb = int(cols[1]) / 1024 / 1024
                                disk_used_gb = int(cols[2]) / 1024 / 1024
                                disk_avail_gb = int(cols[3]) / 1024 / 1024
                            except (ValueError, IndexError):
                                pass
                    # 计算当前用户在该路径下的磁盘占用（du -sk）
                    user_used_gb: float | None = None
                    du_out, _, du_rc = _ssh_exec(
                        client,
                        f"du -sk {safe_path} 2>/dev/null | awk '{{print $1}}'",
                        timeout=15,
                    )
                    if du_rc == 0 and du_out.strip().isdigit():
                        user_used_gb = int(du_out.strip()) / 1024 / 1024
                    paths_info.append({
                        "path": path,
                        "totalGb": disk_total_gb,
                        "usedGb": disk_used_gb,
                        "availGb": disk_avail_gb,
                        "pathUsedGb": user_used_gb,
                    })
            finally:
                client.close()

        # ---------- 镜像配额（配额占用者均分） ----------
        img_usage = _calc_user_image_usage(conn, srv_row, user)

        # ---------- 容器数量配额 ----------
        ctr_usage = _calc_user_container_usage(conn, srv_row, user)

        # ---------- CUDA 权限 ----------
        server_gpu_info: list[dict[str, Any]] = json.loads(srv_row["gpu_info"]) if srv_row["gpu_info"] else []
        cuda_available = bool(srv_row["cuda_available"])
        if user.role == "admin":
            # 管理员拥有所有 GPU
            allowed_gpu_indices = [g["index"] for g in server_gpu_info]
        else:
            allowed_gpu_indices: list[int] = perms.get("cuda_gpu_indices", [])

        # 拼装用户可用的 GPU 详情
        gpu_index_set = set(allowed_gpu_indices)
        available_gpus = [g for g in server_gpu_info if g["index"] in gpu_index_set]

        # 计算被容器占用的 GPU 数量（当前用户有权看到的容器）
        gpu_used_count = 0
        gpu_total_count = len(server_gpu_info)

    return {
        "serverId": server_id,
        "volume": {
            "quotaGb": vol_usage["quotaGb"],
            "usedSelfGb": vol_usage["usedSelfGb"],
            "usedTotalGb": vol_usage["usedTotalGb"],
            "remainingGb": vol_usage["remainingGb"],
            "countSelf": vol_usage["countSelf"],
            "countTotal": vol_usage["countTotal"],
        },
        "image": {
            "quotaGb": img_usage["quotaGb"],
            "usedSelfGb": img_usage["usedSelfGb"],
            "usedTotalGb": img_usage["usedTotalGb"],
            "remainingGb": img_usage["remainingGb"],
            "countSelf": img_usage["countSelf"],
            "countTotal": img_usage["countTotal"],
        },
        "container": {
            "quotaNum": ctr_usage["quotaNum"],
            "usedSelf": ctr_usage["usedSelf"],
            "usedTotal": ctr_usage["usedTotal"],
            "remaining": ctr_usage["remaining"],
        },
        "paths": paths_info,
        "cuda": {
            "serverHasCuda": cuda_available,
            "allowedGpuIndices": allowed_gpu_indices,
            "availableGpus": available_gpus,
            "totalGpuCount": gpu_total_count,
            "allGpuInfo": server_gpu_info,
        },
    }


# ==============================================================
# 宿主机目录浏览（供 host_path 变量点选）
# ==============================================================

def browse_host_dirs(server_id: str, path: str, user: User) -> dict[str, Any]:
    """列出服务器上指定目录的子目录（仅目录，不含文件），供前端路径选择器使用。

    - 受用户 ctr_path_whitelist 限制：只能浏览白名单内的路径。
      若 path 不在任一白名单前缀下，返回 403。
      管理员白名单为空表示不受限。
    - path 为空或 "/" 时，若用户有白名单则返回白名单根目录列表，否则返回根目录。
    - 返回 { path, dirs: [{ name, path }] }，dirs 为可直接点选的子目录。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        srv_row = _get_server_row(conn, server_id)
        perms = _get_user_perms(conn, server_id, user)

    # 管理员路径不受限；普通用户受 ctr_path_whitelist 限制
    whitelist: list[str] = perms.get("ctr_path_whitelist", []) if user.role != "admin" else []
    # 规范化白名单：去尾 /，空字符串过滤
    whitelist = [w.rstrip("/") for w in whitelist if w and w.strip()]

    # 确定要浏览的路径
    target = (path or "").strip() or "/"
    if not target.startswith("/"):
        target = "/" + target
    # 规范化：去除多余的 //
    target = "/" + "/".join(p for p in target.split("/") if p)

    # 若用户有白名单，校验 target 是否在白名单前缀下
    if whitelist:
        in_whitelist = any(target == w or target.startswith(w + "/") for w in whitelist)
        if not in_whitelist:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"路径 {target} 不在您可浏览的白名单范围内",
                status_code=403,
                tool_id=TOOL_ID,
            )

    client = _ssh_connect(srv_row)
    dirs: list[dict[str, Any]] = []
    try:
        safe = shlex.quote(target)
        # 列出目录下的子目录（仅一层），排除隐藏目录和符号链接文件
        # 使用 find 限制 maxdepth 1 且仅 type d，避免 ls 的权限/格式问题
        cmd = (
            f"find {safe} -maxdepth 1 -type d "
            f"! -name '.*' 2>/dev/null | sort"
        )
        out, _, rc = _ssh_exec(client, cmd, timeout=15)
        if rc == 0:
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            # find 结果第一行通常是 target 本身，跳过
            for line in lines:
                if line == target:
                    continue
                name = line.rsplit("/", 1)[-1] if "/" in line else line
                if not name:
                    continue
                dirs.append({"name": name, "path": line})
    finally:
        client.close()

    return {"path": target, "dirs": dirs}
