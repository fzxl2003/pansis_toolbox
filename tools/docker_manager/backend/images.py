from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})

# ==============================================================
# 镜像管理
# ==============================================================

def _calc_user_image_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的镜像使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的镜像，按该镜像的 quota_holder 数量均分大小。
    SSH 连接失败时各项用量按 0 处理（不阻塞调用方）。

    返回:
        quotaGb      配额上限 GB（0=不限）
        usedSelfGb   当前用户配额占用 GB（按配额占用者均分）
        usedTotalGb  服务器全部镜像 GB
        countSelf    当前用户作为配额占用者的镜像数
        countTotal   服务器全部镜像数
        remainingGb  剩余配额 GB（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    img_quota_gb = float(perms.get("img_quota_gb", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的镜像 ref 集合
    user_qh_img_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_img_refs.add(r["resource_ref"])
    # 构建镜像 ref → quota_holder 数量的映射（仅用户是 quota_holder 的镜像）
    img_qh_counts: dict[str, int] = {}
    for ref in user_qh_img_refs:
        cnt_row = conn.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM docker_resource_roles
               WHERE server_id=? AND resource_type='image' AND resource_ref=? AND role='quota_holder'""",
            (server_row["id"], ref),
        ).fetchone()
        img_qh_counts[ref] = max(1, cnt_row["cnt"])

    img_used_self_gb = 0.0
    img_used_total_gb = 0.0
    img_count_self = 0
    img_count_total = 0
    for row in conn.execute(
        "SELECT image_ref, size_gb FROM docker_df_images WHERE server_id=?",
        (server_row["id"],),
    ).fetchall():
        ref = row["image_ref"]
        size_gb = float(row["size_gb"] or 0)
        img_used_total_gb += size_gb
        img_count_total += 1
        if ref in user_qh_img_refs:
            img_used_self_gb += size_gb / img_qh_counts[ref]
            img_count_self += 1

    img_remaining_gb = max(0.0, img_quota_gb - img_used_self_gb) if img_quota_gb > 0 else None
    return {
        "quotaGb": img_quota_gb,
        "usedSelfGb": img_used_self_gb,
        "usedTotalGb": img_used_total_gb,
        "countSelf": img_count_self,
        "countTotal": img_count_total,
        "remainingGb": img_remaining_gb,
    }


def _enforce_image_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的镜像配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于拉取镜像 / 跨服务器复制镜像（目标服务器）前的配额校验。
    管理员不受限（quota_gb=0 表示不限）。
    """
    usage = _calc_user_image_usage(conn, server_row, user)
    quota_gb = float(usage["quotaGb"])
    used_self_gb = float(usage["usedSelfGb"])
    if quota_gb > 0 and used_self_gb >= quota_gb:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"镜像空间配额不足，已用 {used_self_gb:.2f} GB，配额 {quota_gb:.2f} GB",
            status_code=403,
            tool_id=TOOL_ID,
        )


def list_images(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器上的 Docker 镜像，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 需要 img_use 或 img_view_all 或管理员
        if user.role != "admin" and not perms.get("img_use") and not perms.get("img_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看镜像的权限", status_code=403, tool_id=TOOL_ID)
        # 获取镜像所有权元数据
        meta_rows = conn.execute(
            "SELECT image_ref, owner_user_id FROM docker_images_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["image_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的所有镜像 ref。creator/quota_holder 不具备查看权
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role IN ('owner','viewer')",
            (server_id, user.id),
        ).fetchall():
            user_accessible_refs.add(r["resource_ref"])
        # 查询当前用户拥有 owner 角色的镜像 ref（可管理=可删除/复制）。creator 不拥有管理权，仅 owner 可管理
        user_managed_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role='owner'",
            (server_id, user.id),
        ).fetchall():
            user_managed_refs.add(r["resource_ref"])
        # 是否有全局镜像管理权限
        has_img_manage_all = user.role == "admin" or bool(perms.get("img_manage_all"))
        # 角色继承：查询用户有访问权的容器，通过缓存表找到这些容器使用的镜像
        # 规则：容器的 owner/viewer 自动继承该容器使用的镜像的查看权（creator/quota_holder 不继承）
        if user.role != "admin":
            accessible_ctrs: set[str] = set()
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["resource_ref"])
            # _meta 表：作为容器 owner 的也算
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["container_ref"])
            if accessible_ctrs:
                # 从缓存表查找这些容器关联的镜像
                placeholders = ",".join("?" * len(accessible_ctrs))
                for r in conn.execute(
                    f"SELECT resource_ref FROM docker_container_resource_cache WHERE server_id=? AND resource_type='image' AND container_ref IN ({placeholders})",
                    [server_id, *accessible_ctrs],
                ).fetchall():
                    user_accessible_refs.add(r["resource_ref"])
        view_all = user.role == "admin" or perms.get("img_view_all", False) or perms.get("ctr_view_all", False)
        cache_rows = conn.execute(
            "SELECT * FROM docker_df_images WHERE server_id=? ORDER BY repository, tag",
            (server_id,),
        ).fetchall()

    images = []
    for cache in cache_rows:
        # 查找所有者：优先用 repo:tag 匹配，其次用 image id 匹配
        ref_full = cache["image_ref"]
        ref_candidates = _resource_ref_candidates("image", ref_full) + _resource_ref_candidates("image", cache["image_id"])
        owner = next((meta_map.get(ref) for ref in ref_candidates if meta_map.get(ref)), None)
        img = {
            "id": cache["image_id"],
            "repo": cache["repository"],
            "tag": cache["tag"],
            "size": cache["size"],
            "created": cache["created"],
            "sharedSize": cache["shared_size"],
            "uniqueSize": cache["unique_size"],
            "containers": cache["containers"],
            "ownerUserId": owner,
            "platformManaged": owner is not None or any(ref in user_accessible_refs for ref in ref_candidates),
        }
        # 当前用户是否可管理该镜像（删除/复制）：全局管理权 或 owner 角色。creator 不拥有管理权
        img["canManage"] = has_img_manage_all or any(ref in user_managed_refs for ref in ref_candidates)
        # 该镜像是否被服务器上任意容器使用（不受权限过滤，用于禁用删除按钮）
        img["inUse"] = int(cache["containers"] or 0) > 0

        # 访问过滤：view_all 看全部；否则看有查看角色关联的（owner/viewer）
        if view_all:
            images.append(img)
        elif any(ref in user_accessible_refs for ref in ref_candidates) or owner == user.id:
            images.append(img)

    return images


def pull_image(server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """在服务器上拉取 Docker 镜像"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not perms.get("img_pull"):
                raise ToolboxError("PERMISSION_DENIED", "您没有拉取镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 镜像配额校验：拉取会在本服务器新增镜像并占用配额，配额已满则禁止拉取
        if user.role != "admin":
            _enforce_image_quota(conn, row, user)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker pull {shlex.quote(image_ref)}", timeout=300)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("PULL_FAILED", f"镜像拉取失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 记录创建者+所有者（image_ref 使用 repo:tag 格式）
    image_ref = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _record_resource_creator(conn, server_id, "image", image_ref, user.id)

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "output": stdout + stderr}


def delete_image(server_id: str, image_ref: str, user: User, force: bool = False) -> dict[str, Any]:
    """删除服务器上的 Docker 镜像（需 img_use 权限，且须是所有者或 img_manage_all）"""
    init_docker_database()
    normalized_ref = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 有 img_manage_all 权限的可删除任意镜像
            if not perms.get("img_manage_all"):
                # 没有 img_manage_all 的需要 img_use 权限
                if not perms.get("img_use"):
                    raise ToolboxError(
                        "PERMISSION_DENIED",
                        "您没有使用镜像的权限，无法删除镜像",
                        status_code=403, tool_id=TOOL_ID,
                    )
                # 且只能删除自己拥有 owner 角色的镜像（creator 不拥有管理权）
                if not _user_can_manage_resource(conn, server_id, "image", normalized_ref, user):
                    raise ToolboxError("PERMISSION_DENIED", "您只能删除自己拥有的镜像，如需删除他人镜像请申请「管理所有用户的镜像」权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        flag = "-f " if force else ""
        stdout, stderr, code = _ssh_exec(client, f"docker rmi {flag}{shlex.quote(normalized_ref)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_IMAGE_FAILED", f"删除镜像失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据
    with get_connection() as conn:
        _delete_resource_metadata(conn, server_id, "image", _resource_ref_candidates("image", normalized_ref))
    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "output": stdout + stderr}


def copy_image(src_server_id: str, dst_server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """
    跨服务器复制镜像：docker save | gzip -> 平台中转 -> gunzip | docker load
    整个过程在平台服务器内存中流式处理，避免落盘大文件
    """
    init_docker_database()
    image_ref_norm = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _require_server_visible(conn, src_server_id, user)
        _require_server_visible(conn, dst_server_id, user)
        if user.role != "admin":
            # 源服务器需要 img_copy 权限（跨服务器复制镜像）
            # 注意：img_copy 是跨服务器复制的专用权限，img_manage_all / 镜像所有者均不能绕过
            src_perms = _get_user_perms(conn, src_server_id, user)
            if not src_perms.get("img_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在源服务器没有「跨服务器复制镜像」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            # 目标服务器需要 img_copy 权限（跨服务器复制镜像）
            dst_perms = _get_user_perms(conn, dst_server_id, user)
            if not dst_perms.get("img_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在目标服务器没有「跨服务器复制镜像」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            can_view_source = (
                src_perms.get("img_view_all")
                or src_perms.get("img_manage_all")
                or _user_can_access_resource(conn, src_server_id, "image", image_ref_norm, user)
            )
            if not can_view_source:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有访问源镜像的权限，无法复制该镜像",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)
        # 目标服务器镜像配额校验：复制会在目标服务器新增镜像并占用配额，配额已满则禁止作为复制目标
        if user.role != "admin":
            _enforce_image_quota(conn, dst_row, user)

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    src_client = _ssh_connect(src_row)
    dst_client = _ssh_connect(dst_row)

    try:
        # 在源端执行 docker save | gzip
        src_transport = src_client.get_transport()
        dst_transport = dst_client.get_transport()

        src_chan = src_transport.open_session()
        dst_chan = dst_transport.open_session()

        src_chan.exec_command(f"docker save {shlex.quote(image_ref_norm)} | gzip")
        dst_chan.exec_command("gunzip | docker load")

        # 流式传输数据
        total_bytes = 0
        while True:
            data = src_chan.recv(65536)
            if not data:
                break
            total_bytes += len(data)
            dst_chan.sendall(data)

        dst_chan.shutdown_write()
        dst_exit = dst_chan.recv_exit_status()
        src_exit = src_chan.recv_exit_status()

        if src_exit != 0:
            src_err = src_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError("COPY_SRC_FAILED", f"源端保存镜像失败: {src_err}", status_code=502, tool_id=TOOL_ID)
        if dst_exit != 0:
            dst_err = dst_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError("COPY_DST_FAILED", f"目标端加载镜像失败: {dst_err}", status_code=502, tool_id=TOOL_ID)

        # 在目标服务器记录创建者+所有者（默认为复制者）
        with get_connection() as conn:
            _record_resource_creator(conn, dst_server_id, "image", image_ref_norm, user.id)

        _refresh_docker_df_cache_best_effort(dst_server_id)
        return {
            "success": True,
            "imageRef": image_ref,
            "transferredBytes": total_bytes,
        }
    finally:
        src_client.close()
        dst_client.close()


