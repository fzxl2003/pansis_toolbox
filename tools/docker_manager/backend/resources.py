from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})

# ==============================================================
# 资源多角色管理（管理员专用）
# ==============================================================

def _get_resource_roles(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str) -> dict[str, Any]:
    """
    从 docker_resource_roles 表中查询一个资源的所有角色信息。
    返回：{ "ownerUserIds": [...], "viewerUserIds": [...], "creatorUserId": str|None, "quotaHolderUserIds": [...] }
    """
    rows = conn.execute(
        "SELECT user_id, role FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=?",
        (server_id, resource_type, resource_ref),
    ).fetchall()
    owner_ids: list[str] = []
    viewer_ids: list[str] = []
    quota_holder_ids: list[str] = []
    creator_id: str | None = None
    for r in rows:
        if r["role"] == "owner":
            owner_ids.append(r["user_id"])
        elif r["role"] == "viewer":
            viewer_ids.append(r["user_id"])
        elif r["role"] == "creator":
            creator_id = r["user_id"]
        elif r["role"] == "quota_holder":
            quota_holder_ids.append(r["user_id"])
    return {
        "ownerUserIds": owner_ids,
        "viewerUserIds": viewer_ids,
        "creatorUserId": creator_id,
        "quotaHolderUserIds": quota_holder_ids,
    }


def _user_has_resource_access(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str, user_id: str) -> bool:
    """
    判断用户是否能查看（访问）某资源：仅 owner/viewer 角色具备查看权。
    creator 和 quota_holder 不是有权限角色，不授予任何权限。
    """
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM docker_resource_roles
           WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=? AND role IN ('owner','viewer')""",
        (server_id, resource_type, resource_ref, user_id),
    ).fetchone()
    return bool(row["cnt"] > 0)


def list_server_resources(server_id: str, user: User) -> dict[str, Any]:
    """
    列出服务器上的全部容器、镜像、卷，并附上平台侧多角色信息（仅管理员）。
    使用 docker system df -v 缓存，再与本地元数据合并。
    """
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以查看资源所有者信息", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 拉取新的多角色表数据
        role_rows = conn.execute(
            "SELECT resource_type, resource_ref, user_id, role FROM docker_resource_roles WHERE server_id=?",
            (server_id,),
        ).fetchall()
        # 构建 roles_map: { (resource_type, resource_ref) -> {owner:[...], viewer:[...], creator:str, quota_holder:[...]} }
        roles_map: dict[tuple[str,str], dict] = {}
        for r in role_rows:
            key = (r["resource_type"], r["resource_ref"])
            if key not in roles_map:
                roles_map[key] = {"ownerUserIds": [], "viewerUserIds": [], "creatorUserId": None, "quotaHolderUserIds": []}
            if r["role"] == "owner":
                roles_map[key]["ownerUserIds"].append(r["user_id"])
            elif r["role"] == "viewer":
                roles_map[key]["viewerUserIds"].append(r["user_id"])
            elif r["role"] == "creator":
                roles_map[key]["creatorUserId"] = r["user_id"]
            elif r["role"] == "quota_holder":
                roles_map[key]["quotaHolderUserIds"].append(r["user_id"])

        # 从 _meta 表读取 owner 和 is_public（显示用）
        img_meta = {r["image_ref"]: dict(r) for r in
                    conn.execute("SELECT image_ref, owner_user_id, is_public FROM docker_images_meta WHERE server_id=?", (server_id,)).fetchall()}
        ctr_meta = {r["container_ref"]: dict(r) for r in
                    conn.execute("SELECT container_ref, owner_user_id, is_public FROM docker_containers_meta WHERE server_id=?", (server_id,)).fetchall()}
        vol_meta = {r["volume_name"]: dict(r) for r in
                    conn.execute("SELECT volume_name, owner_user_id, is_public FROM docker_volumes_meta WHERE server_id=?", (server_id,)).fetchall()}
        ctr_rows = conn.execute("SELECT * FROM docker_df_containers WHERE server_id=? ORDER BY names", (server_id,)).fetchall()
        img_rows = conn.execute("SELECT * FROM docker_df_images WHERE server_id=? ORDER BY repository, tag", (server_id,)).fetchall()
        vol_rows = conn.execute("SELECT * FROM docker_df_volumes WHERE server_id=? ORDER BY volume_name", (server_id,)).fetchall()

    def _merge_roles(rtype: str, ref: str, legacy_owner: str | None) -> dict:
        """合并新角色表和旧 owner 字段"""
        base = roles_map.get((rtype, ref), {"ownerUserIds": [], "viewerUserIds": [], "creatorUserId": None, "quotaHolderUserIds": []})
        # 旧数据只有 owner_user_id，将其归入 ownerUserIds（如果新表没有对应记录）
        if legacy_owner and legacy_owner not in base["ownerUserIds"]:
            base["ownerUserIds"] = [legacy_owner] + base["ownerUserIds"]
        platform_managed = bool(base["ownerUserIds"] or base["viewerUserIds"] or base["creatorUserId"] or legacy_owner)
        return {**base, "platformManaged": platform_managed}

    containers = []
    for row in ctr_rows:
        name = row["names"] or row["container_id"][:12]
        id_short = row["container_id"][:12]
        ref = name or id_short
        meta_entry = ctr_meta.get(name) or ctr_meta.get(id_short)
        legacy_owner = meta_entry["owner_user_id"] if meta_entry else None
        is_public = bool(meta_entry["is_public"]) if meta_entry else False
        roles = _merge_roles("container", ref, legacy_owner)
        containers.append({
            "ID": id_short,
            "Names": name,
            "Image": row["image"],
            "Command": row["command"],
            "Status": row["status"],
            "State": row["status"].split(" ", 1)[0].lower() if row["status"] else "",
            "Ports": row["ports"],
            "CreatedAt": row["created"],
            "Size": row["size"],
            "LocalVolumes": row["local_volumes"],
            "isPublic": is_public,
            **roles,
        })

    images = []
    for row in img_rows:
        ref_full = row["image_ref"]
        meta_entry = img_meta.get(ref_full) or img_meta.get(row["image_id"])
        legacy_owner = meta_entry["owner_user_id"] if meta_entry else None
        is_public = bool(meta_entry["is_public"]) if meta_entry else False
        roles = _merge_roles("image", ref_full, legacy_owner)
        images.append({
            "id": row["image_id"],
            "repo": row["repository"],
            "tag": row["tag"],
            "size": row["size"],
            "created": row["created"],
            "sharedSize": row["shared_size"],
            "uniqueSize": row["unique_size"],
            "containers": row["containers"],
            "isPublic": is_public,
            **roles,
        })

    volumes = []
    for row in vol_rows:
        name = row["volume_name"]
        meta_entry = vol_meta.get(name)
        legacy_owner = meta_entry["owner_user_id"] if meta_entry else None
        is_public = bool(meta_entry["is_public"]) if meta_entry else False
        roles = _merge_roles("volume", name, legacy_owner)
        volumes.append({"name": name, "size": row["size"], "links": row["links"], "isPublic": is_public, **roles})

    return {"serverId": server_id, "containers": containers, "images": images, "volumes": volumes}


def assign_resource_roles(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    owner_user_ids: list[str],
    viewer_user_ids: list[str],
    creator_user_id: str,
    user: User,
    quota_holder_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    设置服务器资源的多角色绑定（管理员专用）。
    - owner_user_ids: 所有者列表（可多人）
    - viewer_user_ids: 查看者列表（可多人）
    - creator_user_id: 创建者（唯一，传 "" 表示不设置）
    - quota_holder_user_ids: 配额占用者（可多人，无需是所有者）

    逻辑：
    - 创建者默认拥有所有者权限（即同时出现在 owner 角色中）
    - 如果管理员从 owner_user_ids 中移除创建者，创建者失去所有者权限（但 creator 角色保留）
    - viewer 不需要额外 owner 权限
    - quota_holder 不需要是 owner（配额占用者独立于所有者）
    """
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以分配资源角色", status_code=403, tool_id=TOOL_ID)
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    if quota_holder_user_ids is None:
        quota_holder_user_ids = []

    now = _now()

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 校验所有用户 ID 的存在性
        all_user_ids = set(owner_user_ids) | set(viewer_user_ids) | set(quota_holder_user_ids)
        if creator_user_id:
            all_user_ids.add(creator_user_id)
        for uid in all_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        # 保留已有的 creator 记录（创建者由系统自动写入，管理员无法通过此接口修改）
        existing_creator = conn.execute(
            "SELECT user_id FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='creator'",
            (server_id, resource_type, resource_ref),
        ).fetchone()

        # 清除 owner / viewer / quota_holder 记录（保留 creator）
        conn.execute(
            "DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role IN ('owner','viewer','quota_holder')",
            (server_id, resource_type, resource_ref),
        )

        # 写入新的 owner / viewer / quota_holder 记录
        for uid in owner_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "owner", now),
                )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "viewer", now),
                )
        for uid in quota_holder_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "quota_holder", now),
                )
        # 只有当显式传入 creatorUserId 时才写入（通常仅由创建资源的接口设置）
        if creator_user_id:
            conn.execute(
                "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                (_new_id(), server_id, resource_type, resource_ref, creator_user_id, "creator", now),
            )
        # 合并：使用已有 creator（如果本次未传）
        effective_creator = creator_user_id or (existing_creator["user_id"] if existing_creator else "")

        # 同步 _meta 表（使用第一个 owner 用于查询）
        first_owner = owner_user_ids[0] if owner_user_ids else ""
        _sync_legacy_meta(conn, server_id, resource_type, resource_ref, first_owner, now)

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": resource_ref,
        "ownerUserIds": owner_user_ids,
        "viewerUserIds": viewer_user_ids,
        "quotaHolderUserIds": quota_holder_user_ids,
        "creatorUserId": effective_creator or None,
        "assignedAt": now,
    }


def _sync_legacy_meta(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str, owner_user_id: str, now: str) -> None:
    """同步到 _meta 表，用于所有权过滤查询。"""
    if resource_type == "image":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_images_meta (id, image_ref, server_id, owner_user_id, assigned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(image_ref, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    assigned_at = excluded.assigned_at
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_images_meta WHERE server_id=? AND image_ref=?",
                (server_id, resource_ref),
            )
    elif resource_type == "container":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_containers_meta (id, container_ref, server_id, owner_user_id, assigned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(container_ref, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    assigned_at = excluded.assigned_at
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                (server_id, resource_ref),
            )
    elif resource_type == "volume":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_volumes_meta (id, volume_name, server_id, owner_user_id, size_gb, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(volume_name, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_volumes_meta WHERE server_id=? AND volume_name=?",
                (server_id, resource_ref),
            )


# assign_resource_owner（内部转为新逻辑）
def assign_resource_owner(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    owner_user_id: str,
    user: User,
) -> dict[str, Any]:
    """单 owner 分配，内部转为多角色逻辑。"""
    owner_ids = [owner_user_id] if owner_user_id else []
    return assign_resource_roles(server_id, resource_type, resource_ref, owner_ids, [], "", user)


def list_my_owned_resources(user: User) -> list[dict[str, Any]]:
    """
    返回当前用户作为 owner 的所有资源（跨所有服务器），并附带每个资源的 viewer 列表。
    供非管理员用户在「我的资源」页面查看和管理查看者权限。
    """
    init_docker_database()
    with get_connection() as conn:
        # 查询当前用户作为 owner 的所有资源
        owned_rows = conn.execute(
            """SELECT server_id, resource_type, resource_ref
               FROM docker_resource_roles
               WHERE user_id=? AND role='owner'
               ORDER BY server_id, resource_type, resource_ref""",
            (user.id,),
        ).fetchall()

        if not owned_rows:
            return []

        # 收集涉及的 server_id 列表，批量查询服务器名称
        server_ids = list({r["server_id"] for r in owned_rows})
        server_map: dict[str, str] = {}
        for sid in server_ids:
            row = conn.execute("SELECT id, name FROM docker_servers WHERE id=?", (sid,)).fetchone()
            if row:
                server_map[row["id"]] = row["name"]

        # 对每个资源查询其 viewer 和 creator 列表
        results = []
        for r in owned_rows:
            roles = _get_resource_roles(conn, r["server_id"], r["resource_type"], r["resource_ref"])
            # 把 viewerUserIds 转换为含用户名的列表
            viewer_details = []
            for vid in roles.get("viewerUserIds", []):
                u = conn.execute("SELECT id, username, display_name FROM users WHERE id=?", (vid,)).fetchone()
                if u:
                    viewer_details.append({
                        "userId": u["id"],
                        "username": u["username"],
                        "displayName": u["display_name"],
                    })
            results.append({
                "serverId": r["server_id"],
                "serverName": server_map.get(r["server_id"], r["server_id"]),
                "resourceType": r["resource_type"],
                "resourceRef": r["resource_ref"],
                "creatorUserId": roles.get("creatorUserId"),
                "viewerUserIds": roles.get("viewerUserIds", []),
                "viewers": viewer_details,
            })
    return results


def set_resource_viewers(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    viewer_user_ids: list[str],
    user: User,
) -> dict[str, Any]:
    """
    允许资源所有者（owner）修改资源的查看者（viewer）列表。
    - 只能设置 viewer，不能修改 owner 或 creator
    - 调用者必须是该资源的 owner（管理员也可以）
    """
    init_docker_database()
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    now = _now()

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 校验调用者是否为该资源的 owner（或管理员）
        if user.role != "admin":
            is_owner = conn.execute(
                "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=? AND role='owner'",
                (server_id, resource_type, resource_ref, user.id),
            ).fetchone()
            if not is_owner:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您不是该资源的所有者，无法修改查看者列表",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        # 校验 viewer 用户存在性
        for uid in viewer_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        # 不能把 owner 自己加入 viewer（没有意义）
        owner_ids = {r["user_id"] for r in conn.execute(
            "SELECT user_id FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='owner'",
            (server_id, resource_type, resource_ref),
        ).fetchall()}
        viewer_user_ids = [uid for uid in viewer_user_ids if uid not in owner_ids]

        # 只清除 viewer 记录，保留 owner / creator
        conn.execute(
            "DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='viewer'",
            (server_id, resource_type, resource_ref),
        )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "viewer", now),
                )

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": resource_ref,
        "viewerUserIds": viewer_user_ids,
        "updatedAt": now,
    }


_MANAGE_ALL_PERM_KEY: dict[str, str] = {
    "image": "img_manage_all",
    "container": "ctr_manage_all",
    "volume": "vol_manage_all",
}


def set_resource_public(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    is_public: bool,
    user: User,
) -> dict[str, Any]:
    """设置资源的公开状态（is_public）。

    权限要求（满足任一即可）：
      - 管理员
      - 拥有该资源类型的全局管理权（img_manage_all / ctr_manage_all / vol_manage_all）
      - 该资源的 owner（通过 _user_can_manage_resource 判断）

    若资源尚无 meta 记录，则以调用者作为 owner 创建一条，再更新 is_public。
    """
    init_docker_database()
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    now = _now()
    public_val = 1 if is_public else 0

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 权限校验
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            manage_all_key = _MANAGE_ALL_PERM_KEY.get(resource_type, "")
            has_manage_all = bool(perms.get(manage_all_key))
            is_owner = _user_can_manage_resource(conn, server_id, resource_type, resource_ref, user)
            if not has_manage_all and not is_owner:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有管理此资源的权限，无法修改公开状态（需要是资源所有者或拥有全局管理权限）",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        # 确保资源标识规范化（镜像需要 normalize）
        ref = resource_ref
        if resource_type == "image":
            ref = _normalize_image_ref(resource_ref)

        # 确保有 meta 记录：若无则以调用者为 owner 创建
        if resource_type == "image":
            existing = conn.execute(
                "SELECT 1 FROM docker_images_meta WHERE server_id=? AND image_ref=?",
                (server_id, ref),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO docker_images_meta (id, image_ref, server_id, owner_user_id, assigned_at, is_public) VALUES (?,?,?,?,?,?)",
                    (_new_id(), ref, server_id, user.id, now, public_val),
                )
            else:
                conn.execute(
                    "UPDATE docker_images_meta SET is_public=? WHERE server_id=? AND image_ref=?",
                    (public_val, server_id, ref),
                )
        elif resource_type == "container":
            existing = conn.execute(
                "SELECT 1 FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                (server_id, ref),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO docker_containers_meta (id, container_ref, server_id, owner_user_id, assigned_at, is_public) VALUES (?,?,?,?,?,?)",
                    (_new_id(), ref, server_id, user.id, now, public_val),
                )
            else:
                conn.execute(
                    "UPDATE docker_containers_meta SET is_public=? WHERE server_id=? AND container_ref=?",
                    (public_val, server_id, ref),
                )
        elif resource_type == "volume":
            existing = conn.execute(
                "SELECT 1 FROM docker_volumes_meta WHERE server_id=? AND volume_name=?",
                (server_id, ref),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO docker_volumes_meta (id, volume_name, server_id, owner_user_id, size_gb, created_at, is_public) VALUES (?,?,?,?,0,?,?)",
                    (_new_id(), ref, server_id, user.id, now, public_val),
                )
            else:
                conn.execute(
                    "UPDATE docker_volumes_meta SET is_public=? WHERE server_id=? AND volume_name=?",
                    (public_val, server_id, ref),
                )

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": ref,
        "isPublic": bool(public_val),
        "updatedAt": now,
    }


