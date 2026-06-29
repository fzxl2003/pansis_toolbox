from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})

# ==============================================================
# 卷管理
# ==============================================================

def _calc_user_volume_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的卷使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的卷，按该卷的 quota_holder 数量均分大小。

    返回:
        quotaGb      配额上限 GB（0=不限）
        usedSelfGb   当前用户配额占用 GB（按配额占用者均分）
        usedTotalGb  服务器全部卷 GB
        countSelf    当前用户作为配额占用者的卷数
        countTotal   服务器全部卷数
        remainingGb  剩余配额 GB（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    vol_quota_gb = float(perms.get("vol_quota_gb", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的卷 ref 集合
    user_qh_vol_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_vol_refs.add(r["resource_ref"])
    # 构建卷 ref → quota_holder 数量的映射（仅用户是 quota_holder 的卷）
    vol_qh_counts: dict[str, int] = {}
    for ref in user_qh_vol_refs:
        cnt_row = conn.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM docker_resource_roles
               WHERE server_id=? AND resource_type='volume' AND resource_ref=? AND role='quota_holder'""",
            (server_row["id"], ref),
        ).fetchone()
        vol_qh_counts[ref] = max(1, cnt_row["cnt"])

    vol_used_self_gb = 0.0
    vol_used_total_gb = 0.0
    vol_count_self = len(user_qh_vol_refs)
    vol_count_total = 0
    all_vols = conn.execute(
        "SELECT volume_name, size_gb FROM docker_df_volumes WHERE server_id=?",
        (server_row["id"],),
    ).fetchall()
    for r in all_vols:
        size_gb = float(r["size_gb"] or 0)
        vol_used_total_gb += size_gb
        vol_count_total += 1
        if r["volume_name"] in user_qh_vol_refs:
            vol_used_self_gb += size_gb / vol_qh_counts[r["volume_name"]]

    vol_remaining_gb = max(0.0, vol_quota_gb - vol_used_self_gb) if vol_quota_gb > 0 else None
    return {
        "quotaGb": vol_quota_gb,
        "usedSelfGb": vol_used_self_gb,
        "usedTotalGb": vol_used_total_gb,
        "countSelf": vol_count_self,
        "countTotal": vol_count_total,
        "remainingGb": vol_remaining_gb,
    }


def _enforce_volume_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的卷空间配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于创建卷 / 跨服务器复制卷（目标服务器）前的配额校验。
    管理员不受限（quota_gb=0 表示不限）。
    """
    usage = _calc_user_volume_usage(conn, server_row, user)
    quota_gb = float(usage["quotaGb"])
    used_self_gb = float(usage["usedSelfGb"])
    if quota_gb > 0 and used_self_gb >= quota_gb:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"卷空间配额不足，已用 {used_self_gb:.2f} GB，配额 {quota_gb:.2f} GB",
            status_code=403,
            tool_id=TOOL_ID,
        )


def list_volumes(server_id: str, user: User) -> dict[str, Any]:
    """列出服务器上的卷，附加平台元数据，按所有权/角色过滤（对齐镜像 list_images 模式）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 需要基础卷使用权，或显式的全量查看/管理权限。
        if (
            user.role != "admin"
            and not perms.get("vol_use")
            and not perms.get("vol_view_all")
            and not perms.get("vol_manage_all")
        ):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看卷的权限", status_code=403, tool_id=TOOL_ID)
        # 获取当前用户配额信息（使用 quota_holder 均分逻辑）
        if user.role == "admin":
            quota = {"volumeTotalGb": None, "volumeUsedGb": None}
        else:
            row = _get_server_row(conn, server_id)
            vol_usage = _calc_user_volume_usage(conn, row, user)
            quota = {"volumeTotalGb": vol_usage["quotaGb"], "volumeUsedGb": vol_usage["usedSelfGb"]}

        # 查询平台记录的卷元数据
        meta_rows = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["volume_name"]: dict(r) for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的卷 ref。creator/quota_holder 不具备查看权
        user_accessible_vols: set[str] = set()
        if user.role != "admin":
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                user_accessible_vols.add(r["resource_ref"])
            # 角色继承：查询用户有访问权的容器，通过缓存表找到这些容器挂载的卷
            # 规则：容器的 owner/viewer 自动继承该容器挂载的卷的查看权（creator/quota_holder 不继承）
            accessible_ctrs: set[str] = set()
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["resource_ref"])
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["container_ref"])
            if accessible_ctrs:
                placeholders = ",".join("?" * len(accessible_ctrs))
                for r in conn.execute(
                    f"SELECT resource_ref FROM docker_container_resource_cache WHERE server_id=? AND resource_type='volume' AND container_ref IN ({placeholders})",
                    [server_id, *accessible_ctrs],
                ).fetchall():
                    user_accessible_vols.add(r["resource_ref"])
        # 查询当前用户拥有 owner 角色的卷 ref（可管理=可删除/复制）。creator 不拥有管理权，仅 owner 可管理
        user_managed_vols: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role='owner'",
            (server_id, user.id),
        ).fetchall():
            user_managed_vols.add(r["resource_ref"])
        # 是否有全局卷管理权限
        has_vol_manage_all = user.role == "admin" or bool(perms.get("vol_manage_all"))
        # view_all 只受卷权限控制。ctr_view_all 不能绕过卷的查看边界。
        view_all = user.role == "admin" or perms.get("vol_view_all", False) or perms.get("vol_manage_all", False)
        cache_rows = conn.execute(
            "SELECT * FROM docker_df_volumes WHERE server_id=? ORDER BY volume_name",
            (server_id,),
        ).fetchall()

    volumes = []
    for cache in cache_rows:
        vname = cache["volume_name"]
        vol = {
            "name": vname,
            "driver": "local",
            "mountpoint": "",
            "links": cache["links"],
            "size": cache["size"],
        }
        if vname in meta_map:
            m = meta_map[vname]
            vol["ownerUserId"] = m["owner_user_id"]  # 前端字段
            vol["sizeGb"] = cache["size_gb"]
            vol["createdAt"] = m["created_at"]
            vol["platformManaged"] = True
        else:
            vol["ownerUserId"] = None
            vol["sizeGb"] = cache["size_gb"]
            vol["platformManaged"] = vname in user_accessible_vols

        # 当前用户是否可管理该卷（删除）：全局管理权 或 owner 角色。creator 不拥有管理权
        vol["canManage"] = has_vol_manage_all or vname in user_managed_vols

        # 访问过滤：view_all 看全部；否则看有查看角色关联的（owner/viewer）
        if view_all:
            volumes.append(vol)
        elif vname in user_accessible_vols or vol.get("ownerUserId") == user.id:
            volumes.append(vol)

    return {"volumes": volumes, "quota": quota}


def get_volume_detail(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """
    获取卷详情：包含平台角色信息（creator/owner/viewer）以及挂载该卷的容器列表。
    容器列表按当前用户权限过滤：
      - 有查看权限（owner/viewer/ctr_view_all）的容器：返回完整信息
      - 无查看权限的容器：仅返回数量
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        row = _get_server_row(conn, server_id)

        # 卷平台元数据
        meta = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id=? AND volume_name=?",
            (server_id, volume_name),
        ).fetchone()

        # 新角色表：卷的多角色
        roles = _get_resource_roles(conn, server_id, "volume", volume_name)

        # 当前用户细粒度权限（容器可见性判断用）
        perms = _get_user_perms(conn, server_id, user)
        can_view_volume = (
            user.role == "admin"
            or perms.get("vol_view_all")
            or perms.get("vol_manage_all")
            or (perms.get("vol_use") and _user_can_access_resource(conn, server_id, "volume", volume_name, user))
        )
        if not can_view_volume:
            raise ToolboxError("PERMISSION_DENIED", "您没有权限查看此卷详情", status_code=403, tool_id=TOOL_ID)
        view_all_ctrs = user.role == "admin" or perms.get("ctr_view_all", False)

        # 当前用户可访问的容器 ref 集合（仅 owner/viewer 具备查看权）
        user_accessible_ctrs: set[str] = set()
        if not view_all_ctrs:
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                user_accessible_ctrs.add(r["resource_ref"])
            # _meta 表：owner 也算可见
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                user_accessible_ctrs.add(r["container_ref"])

    # 通过 SSH 查询挂载该卷的所有容器（docker ps -a --filter volume=<name>）
    client = _ssh_connect(row)
    try:
        stdout, _, _ = _ssh_exec(
            client,
            f"docker ps -a --filter volume={shlex.quote(volume_name)} --format '{{{{json .}}}}'",
            timeout=30,
        )
    finally:
        client.close()

    visible_containers: list[dict[str, Any]] = []
    hidden_count = 0
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ctr = json.loads(line)
        except json.JSONDecodeError:
            continue
        ctr_name = ctr.get("Names", "").lstrip("/")
        ctr_id_short = ctr.get("ID", "")[:12]
        ref = ctr_name or ctr_id_short
        if view_all_ctrs or ref in user_accessible_ctrs:
            visible_containers.append({
                "id": ctr.get("ID", ""),
                "name": ctr_name or ctr_id_short,
                "image": ctr.get("Image", ""),
                "status": ctr.get("Status", ""),
                "state": ctr.get("State", ""),
            })
        else:
            hidden_count += 1

    # 组装角色用户名（便于前端直接展示）
    with get_connection() as conn:
        def _get_user_basic(uid: str | None) -> dict | None:
            if not uid:
                return None
            r = conn.execute("SELECT id, username, display_name FROM users WHERE id=?", (uid,)).fetchone()
            return {"userId": r["id"], "username": r["username"], "displayName": r["display_name"]} if r else {"userId": uid, "username": uid, "displayName": uid}

        creator_info = _get_user_basic(roles.get("creatorUserId"))
        owner_infos = [_get_user_basic(uid) for uid in roles.get("ownerUserIds", []) if uid]
        viewer_infos = [_get_user_basic(uid) for uid in roles.get("viewerUserIds", []) if uid]
        quota_holder_infos = [_get_user_basic(uid) for uid in roles.get("quotaHolderUserIds", []) if uid]

    return {
        "serverId": server_id,
        "name": volume_name,
        "sizeGb": meta["size_gb"] if meta else None,
        "createdAt": meta["created_at"] if meta else None,
        "platformManaged": meta is not None or bool(roles.get("ownerUserIds") or roles.get("creatorUserId")),
        "roles": {
            "creatorUserId": roles.get("creatorUserId"),
            "creator": creator_info,
            "ownerUserIds": roles.get("ownerUserIds", []),
            "owners": [o for o in owner_infos if o],
            "viewerUserIds": roles.get("viewerUserIds", []),
            "viewers": [v for v in viewer_infos if v],
            "quotaHolderUserIds": roles.get("quotaHolderUserIds", []),
            "quotaHolders": [q for q in quota_holder_infos if q],
        },
        "mountedContainers": visible_containers,
        "hiddenContainerCount": hidden_count,
    }


def create_volume(server_id: str, name: str, user: User) -> dict[str, Any]:
    """创建 Docker 卷（含权限与配额校验，对齐镜像 pull_image 模式）

    卷大小由 docker system df -v 缓存维护，创建时不预设。
    """
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not perms.get("server_visible") or not perms.get("vol_create"):
                raise ToolboxError("PERMISSION_DENIED", "您没有在此服务器创建卷的权限", status_code=403, tool_id=TOOL_ID)
            # 卷配额校验：创建会在本服务器新增卷并占用配额，配额已满则禁止创建
            row = _get_server_row(conn, server_id)
            _enforce_volume_quota(conn, row, user)
        else:
            row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume create {shlex.quote(name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("CREATE_VOLUME_FAILED", f"创建卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 记录创建者+所有者（对齐镜像 pull_image 中的 _record_resource_creator）
    # size_gb 由 docker system df -v 缓存填充，此处不预设。
    with get_connection() as conn:
        _record_resource_creator(conn, server_id, "volume", name, user.id)

    now = _now()
    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "volumeName": name, "serverId": server_id, "createdAt": now}


def delete_volume(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """删除 Docker 卷（需 vol_use 权限且是卷的 owner 角色，或拥有 vol_manage_all 权限，对齐镜像 delete_image 模式）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("vol_manage_all", False)
            # 有 vol_manage_all 权限的可删除任意卷
            if not can_manage_all:
                # 没有 vol_manage_all 的需要 vol_use 权限
                if not perms.get("vol_use"):
                    raise ToolboxError(
                        "PERMISSION_DENIED",
                        "您没有使用卷的权限，无法删除卷",
                        status_code=403, tool_id=TOOL_ID,
                    )
                # 且只能删除自己拥有 owner 角色的卷（creator 不拥有管理权）
                if not _user_can_manage_resource(conn, server_id, "volume", volume_name, user):
                    raise ToolboxError("PERMISSION_DENIED", "您没有删除此卷的权限（需要是卷的所有者或拥有全局管理权限）", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume rm {shlex.quote(volume_name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_VOLUME_FAILED", f"删除卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据与角色（对齐容器删除时的清理逻辑）
    with get_connection() as conn:
        _delete_resource_metadata(conn, server_id, "volume", _resource_ref_candidates("volume", volume_name))

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "volumeName": volume_name}


def copy_volume(
    src_server_id: str,
    src_volume_name: str,
    dst_server_id: str,
    dst_volume_name: str,
    user: User,
) -> dict[str, Any]:
    """
    跨服务器（或同服务器）复制卷数据（对齐镜像 copy_image 模式）：
      源端: docker run --rm -v <src>:/src:ro alpine tar -czC /src .
      目标端: docker volume create <dst> && docker run --rm -i -v <dst>:/dst alpine sh -c 'tar -xzC /dst'
    整个流程在平台内存中流式中转，不在任何服务器落盘。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, src_server_id, user)
        _require_server_visible(conn, dst_server_id, user)
        if user.role != "admin":
            # 源服务器需要 vol_copy 权限（跨服务器复制卷）
            src_perms = _get_user_perms(conn, src_server_id, user)
            if not src_perms.get("vol_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在源服务器没有「跨服务器复制卷」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            # 目标服务器也需要 vol_copy 权限（对齐镜像 copy_image：源和目标均需 img_copy）
            dst_perms = _get_user_perms(conn, dst_server_id, user)
            if not dst_perms.get("vol_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在目标服务器没有「跨服务器复制卷」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            if not dst_perms.get("vol_create"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "复制卷会在目标服务器创建新卷，您没有目标服务器的创建卷权限",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
            can_view_source = (
                src_perms.get("vol_view_all")
                or src_perms.get("vol_manage_all")
                or _user_can_access_resource(conn, src_server_id, "volume", src_volume_name, user)
            )
            if not can_view_source:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有访问源卷的权限，无法复制该卷",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)

        # 目标服务器卷配额校验：复制会在目标服务器新增卷并占用配额，配额已满则禁止作为复制目标
        if user.role != "admin":
            _enforce_volume_quota(conn, dst_row, user)

        # 获取源卷元数据（用于继承 size_gb）
        src_meta = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id = ? AND volume_name = ?",
            (src_server_id, src_volume_name),
        ).fetchone()

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    same_server = src_server_id == dst_server_id
    src_client = _ssh_connect(src_row)
    dst_client = src_client if same_server else _ssh_connect(dst_row)

    try:
        # 先在目标服务器创建目标卷
        _, stderr, code = _ssh_exec(
            dst_client,
            f"docker volume create {shlex.quote(dst_volume_name)}",
            timeout=30,
        )
        if code != 0:
            raise ToolboxError(
                "CREATE_DST_VOL_FAILED",
                f"创建目标卷失败: {stderr.strip()}",
                status_code=502,
                tool_id=TOOL_ID,
            )

        src_transport = src_client.get_transport()
        dst_transport = dst_client.get_transport()

        src_chan = src_transport.open_session()
        dst_chan = dst_transport.open_session()

        # 源端：把卷内容打包成 tar.gz 输出到 stdout（只读挂载）
        src_cmd = (
            f"docker run --rm -v {shlex.quote(src_volume_name)}:/src:ro alpine "
            f"tar -czC /src ."
        )
        # 目标端：从 stdin 解压到目标卷
        dst_cmd = (
            f"docker run --rm -i -v {shlex.quote(dst_volume_name)}:/dst alpine "
            f"sh -c 'tar -xzC /dst'"
        )

        src_chan.exec_command(src_cmd)
        dst_chan.exec_command(dst_cmd)

        # 流式中转，不落盘
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
            raise ToolboxError(
                "COPY_SRC_FAILED",
                f"源卷打包失败（exit {src_exit}）: {src_err}",
                status_code=502,
                tool_id=TOOL_ID,
            )
        if dst_exit != 0:
            dst_err = dst_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError(
                "COPY_DST_FAILED",
                f"目标卷解压失败（exit {dst_exit}）: {dst_err}",
                status_code=502,
                tool_id=TOOL_ID,
            )

    finally:
        src_client.close()
        if not same_server:
            dst_client.close()

    # 记录目标卷创建者+所有者（对齐镜像 copy_image 中的 _record_resource_creator）
    # size_gb 由 docker system df -v 缓存填充，此处不预设。
    with get_connection() as conn:
        _record_resource_creator(conn, dst_server_id, "volume", dst_volume_name, user.id)

    now = _now()
    _refresh_docker_df_cache_best_effort(dst_server_id)
    return {
        "success": True,
        "srcServerId": src_server_id,
        "srcVolumeName": src_volume_name,
        "dstServerId": dst_server_id,
        "dstVolumeName": dst_volume_name,
        "transferredBytes": total_bytes,
        "createdAt": now,
    }


