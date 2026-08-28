from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})
from .volumes import _calc_user_volume_usage

# ==============================================================
# 服务器管理
# ==============================================================

def add_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    """Bind Docker management to an already-authorized global SSH server."""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以添加服务器", status_code=403, tool_id=TOOL_ID)

    selected = ssh_server_service.get_server(str(payload.get("serverId") or ""), user)
    credentials = ssh_server_service.get_server_credentials(selected["id"], user)
    host = credentials["host"]
    port = int(credentials["port"])
    ssh_username = credentials["ssh_username"]
    name = selected["name"]

    # 验证 SSH 连接和 Docker 权限
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if credentials["auth_type"] == "private_key":
            client.connect(
                hostname=host, port=port, username=ssh_username,
                pkey=ssh_server_service.load_private_key(credentials["private_key"], credentials["private_key_passphrase"], paramiko),
                timeout=15,
            )
        else:
            client.connect(hostname=host, port=port, username=ssh_username, password=credentials["ssh_password"], timeout=15)
    except Exception as exc:
        raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 连接失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc

    cuda_available = False
    gpu_count = 0
    gpu_info: list[dict[str, Any]] = []
    try:
        stdout_data, stderr_data, exit_code = _ssh_exec(client, "docker info", timeout=20)
        if exit_code != 0:
            if "permission denied" in stderr_data.lower() or "permission denied" in stdout_data.lower():
                raise ToolboxError(
                    "DOCKER_PERMISSION_DENIED",
                    f"用户 {ssh_username} 没有 Docker 权限，请将该用户加入 docker 用户组（sudo usermod -aG docker {ssh_username}）",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
            raise ToolboxError(
                "DOCKER_NOT_AVAILABLE",
                f"无法执行 docker 命令: {stderr_data.strip()}",
                status_code=502,
                tool_id=TOOL_ID,
            )

        # 检测 CUDA/nvidia-smi 支持
        cuda_out, _, cuda_rc = _ssh_exec(
            client,
            "docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || "
            "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null",
            timeout=30,
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

    server_id = selected["id"]
    now = _now()

    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM docker_servers WHERE id=?", (server_id,)).fetchone() is not None:
            raise ToolboxError("SERVER_ALREADY_SELECTED", "该全局服务器已添加到 Docker 管理", status_code=409, tool_id=TOOL_ID)
        conn.execute(
            """
            INSERT INTO docker_servers (id, name, host, port, ssh_username, ssh_password_encrypted, created_by, created_at, updated_at, cuda_available, gpu_count, gpu_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (server_id, name, host, port, ssh_username, "", user.id, now, now,
             1 if cuda_available else 0, gpu_count, json.dumps(gpu_info)),
        )
    _refresh_docker_df_cache_best_effort(server_id)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def list_servers(user: User) -> list[dict[str, Any]]:
    """列出用户有权限查看的服务器"""
    init_docker_database()
    with get_connection() as conn:
        all_rows = conn.execute("SELECT * FROM docker_servers ORDER BY name").fetchall()
        result = []
        for row in all_rows:
            try:
                selected = ssh_server_service.get_server(row["id"], user)
            except ToolboxError:
                continue
            perms = _get_user_perms(conn, row["id"], user)
            if user.role != "admin" and not perms.get("server_visible"):
                continue
            srv = _public_server(row)
            srv.update({key: selected[key] for key in ("name", "host", "port", "sshUsername")})
            srv["serverVisible"] = True
            srv["perms"] = perms
            result.append(srv)
    return result


def check_servers_status(user: User) -> dict[str, str]:
    """检测所有可见服务器的 SSH 连接状态（在线/离线）。

    使用短超时（5 秒）做快速连接测试，返回 {server_id: "online"|"offline"} 映射。
    """
    init_docker_database()
    # 获取用户可见的服务器列表
    with get_connection() as conn:
        all_rows = conn.execute("SELECT * FROM docker_servers ORDER BY name").fetchall()
        visible_rows = []
        for row in all_rows:
            try:
                ssh_server_service.get_server(row["id"], user)
            except ToolboxError:
                continue
            perms = _get_user_perms(conn, row["id"], user)
            if user.role == "admin" or perms.get("server_visible"):
                visible_rows.append(row)

    statuses: dict[str, str] = {}
    for row in visible_rows:
        srv_id = row["id"]
        client = None
        try:
            client = ssh_connection_service.borrow_client(_ssh_spec(row, timeout=5))
            statuses[srv_id] = "online"
        except Exception:
            ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=srv_id)
            statuses[srv_id] = "offline"
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
    return statuses


def get_server(server_id: str, user: User) -> dict[str, Any]:
    """获取单个服务器信息（含权限校验）"""
    init_docker_database()
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
        _require_server_visible(conn, server_id, user)
        selected = ssh_server_service.get_server(server_id, user)
        srv = _public_server(row)
        srv.update({key: selected[key] for key in ("name", "host", "port", "sshUsername")})
        srv["perms"] = _get_user_perms(conn, server_id, user)
    return srv


def delete_server(server_id: str, user: User) -> None:
    """删除服务器（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以删除服务器", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        conn.execute("DELETE FROM docker_user_perms WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_volumes_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_images_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_containers_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_resource_roles WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_container_resource_cache WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_df_cache WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_df_images WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_df_containers WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_df_volumes WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_servers WHERE id = ?", (server_id,))
    ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)


def list_server_permissions(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器的用户权限概览（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role
            FROM users u
            WHERE u.disabled = 0
            ORDER BY u.role = 'admin' DESC, u.username
            """,
        ).fetchall()
        result = []
        for row in rows:
            perms = _get_user_perms(conn, server_id, type('_U', (), {'id': row['id'], 'role': row['role']})())
            result.append({
                "userId": row["id"],
                "username": row["username"],
                "displayName": row["display_name"],
                "role": row["role"],
                "perms": perms,
            })
    return result


def get_user_perms_for_user(server_id: str, target_user_id: str, user: User) -> dict[str, Any]:
    """获取指定用户在服务器上的细粒度权限（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise ToolboxError("USER_NOT_FOUND", "目标用户不存在", status_code=404, tool_id=TOOL_ID)
        perms = _get_user_perms(conn, server_id, type('_U', (), {'id': target_user_id, 'role': target['role']})())
    return {"serverId": server_id, "userId": target_user_id, "perms": perms}


def set_user_perms(server_id: str, target_user_id: str, perms: dict[str, Any], user: User) -> dict[str, Any]:
    """设置用户在服务器上的细粒度权限（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise ToolboxError("USER_NOT_FOUND", "目标用户不存在", status_code=404, tool_id=TOOL_ID)
        if target["role"] == "admin":
            raise ToolboxError("ADMIN_IMMUTABLE", "管理员权限不可修改", status_code=400, tool_id=TOOL_ID)

        def b(k: str) -> int:
            return 1 if perms.get(k, False) else 0

        now = _now()
        path_whitelist_json = json.dumps(perms.get("ctr_path_whitelist", []))
        vol_quota_gb = float(perms.get("vol_quota_gb", 0))
        img_quota_gb = float(perms.get("img_quota_gb", 0))
        ctr_quota_num = int(perms.get("ctr_quota_num", 0))
        cuda_gpu_indices_json = json.dumps(perms.get("cuda_gpu_indices", []))
        conn.execute(
            """
            INSERT INTO docker_user_perms (
                server_id, user_id, server_visible,
                img_use, img_pull, img_view_all, img_manage_all, img_copy, img_quota_gb,
                ctr_use, ctr_view_all,
                ctr_create, ctr_create_template,
                ctr_manage_all, ctr_path_whitelist, ctr_quota_num,
                vol_use, vol_view_all, vol_create, vol_manage_all, vol_copy, vol_quota_gb,
                tpl_use, tpl_create, tpl_edit,
                cuda_gpu_indices,
                updated_at
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?,
                ?
            )
            ON CONFLICT(server_id, user_id) DO UPDATE SET
                server_visible = excluded.server_visible,
                img_use = excluded.img_use,
                img_pull = excluded.img_pull,
                img_view_all = excluded.img_view_all,
                img_manage_all = excluded.img_manage_all,
                img_copy = excluded.img_copy,
                img_quota_gb = excluded.img_quota_gb,
                ctr_use = excluded.ctr_use,
                ctr_view_all = excluded.ctr_view_all,
                ctr_create = excluded.ctr_create,
                ctr_create_template = excluded.ctr_create_template,
                ctr_manage_all = excluded.ctr_manage_all,
                ctr_path_whitelist = excluded.ctr_path_whitelist,
                ctr_quota_num = excluded.ctr_quota_num,
                vol_use = excluded.vol_use,
                vol_view_all = excluded.vol_view_all,
                vol_create = excluded.vol_create,
                vol_manage_all = excluded.vol_manage_all,
                vol_copy = excluded.vol_copy,
                vol_quota_gb = excluded.vol_quota_gb,
                tpl_use = excluded.tpl_use,
                tpl_create = excluded.tpl_create,
                tpl_edit = excluded.tpl_edit,
                cuda_gpu_indices = excluded.cuda_gpu_indices,
                updated_at = excluded.updated_at
            """,
            (
                server_id, target_user_id, b("server_visible"),
                b("img_use"), b("img_pull"), b("img_view_all"), b("img_manage_all"), b("img_copy"), img_quota_gb,
                b("ctr_use"), b("ctr_view_all"),
                b("ctr_create"), b("ctr_create_template"),
                b("ctr_manage_all"), path_whitelist_json, ctr_quota_num,
                b("vol_use"), b("vol_view_all"), b("vol_create"), b("vol_manage_all"), b("vol_copy"), vol_quota_gb,
                b("tpl_use"), b("tpl_create"), b("tpl_edit"),
                cuda_gpu_indices_json,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM docker_user_perms WHERE server_id = ? AND user_id = ?",
            (server_id, target_user_id),
        ).fetchone()
    return {"serverId": server_id, "userId": target_user_id, "perms": _row_to_perms(row)}


def get_my_quota(server_id: str, user: User) -> dict[str, Any]:
    """获取当前用户在服务器上的细粒度权限与配额信息"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 计算已用卷空间（使用 quota_holder 均分逻辑，与 list_volumes / get_server_resource_overview 对齐）
        row = _get_server_row(conn, server_id)
        vol_usage = _calc_user_volume_usage(conn, row, user)
        perms["volumeUsedGb"] = vol_usage["usedSelfGb"]
        perms["volumeTotalGb"] = vol_usage["quotaGb"]
        perms["volumeRemainingGb"] = vol_usage["remainingGb"]
    return perms
