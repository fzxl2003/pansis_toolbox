from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})
from .images import _enforce_image_quota
from .volumes import _enforce_volume_quota

# ==============================================================
# 容器管理
# ==============================================================

def _calc_user_container_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的容器使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的容器计入自身用量。
    SSH 连接失败时各项用量按 0 处理（不阻塞调用方）。

    返回:
        quotaNum     配额上限（0=不限）
        usedSelf     当前用户作为 quota_holder 的容器数
        usedTotal    服务器全部容器数
        remaining    剩余配额（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    ctr_quota_num = int(perms.get("ctr_quota_num", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的容器 ref 集合
    user_qh_ctr_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_ctr_refs.add(r["resource_ref"])

    ctr_used_total = conn.execute(
        "SELECT COUNT(*) AS cnt FROM docker_df_containers WHERE server_id=?",
        (server_row["id"],),
    ).fetchone()["cnt"]
    ctr_used_self = len(user_qh_ctr_refs)

    ctr_remaining = max(0, ctr_quota_num - ctr_used_self) if ctr_quota_num > 0 else None
    return {
        "quotaNum": ctr_quota_num,
        "usedSelf": ctr_used_self,
        "usedTotal": ctr_used_total,
        "remaining": ctr_remaining,
    }


def _enforce_container_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的容器数量配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于创建容器前的配额校验。管理员不受限（quota_num=0 表示不限）。
    """
    usage = _calc_user_container_usage(conn, server_row, user)
    quota_num = int(usage["quotaNum"])
    used_self = int(usage["usedSelf"])
    if quota_num > 0 and used_self >= quota_num:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"容器数量配额不足，已有 {used_self} 个容器，配额 {quota_num} 个",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _validate_gpus_permission(gpus_arg: str, allowed_gpu_indices: list[int]) -> None:
    """校验 --gpus 参数是否在用户允许的 GPU 序号范围内。

    支持格式：'all' / 'device=0,1' / 'device=0' / '"device=0,1"'
    """
    if not gpus_arg:
        return
    gpus_arg = gpus_arg.strip().strip('"').strip("'")
    if gpus_arg.lower() == "all":
        # all 表示使用全部 GPU，需要用户拥有全部 GPU 权限
        # 如果 allowed_gpu_indices 为空列表则拒绝（无 GPU 权限）
        if not allowed_gpu_indices:
            raise ToolboxError(
                "GPU_PERMISSION_DENIED",
                "您没有 GPU 使用权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        return
    # 解析 device=0,1 格式
    if gpus_arg.startswith("device="):
        indices_str = gpus_arg[len("device="):]
        try:
            indices = [int(x.strip()) for x in indices_str.split(",") if x.strip()]
        except ValueError:
            raise ToolboxError(
                "INVALID_GPU_ARG",
                f"无效的 GPU 参数: {gpus_arg}",
                status_code=400,
                tool_id=TOOL_ID,
            ) from None
        allowed_set = set(allowed_gpu_indices)
        for idx in indices:
            if idx not in allowed_set:
                raise ToolboxError(
                    "GPU_PERMISSION_DENIED",
                    f"您没有使用 GPU {idx} 的权限，可用 GPU: {allowed_gpu_indices}",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
        return
    # 其他格式不做校验（保守策略：拒绝未知格式）
    raise ToolboxError(
        "INVALID_GPU_ARG",
        f"不支持的 --gpus 参数格式: {gpus_arg}，请使用 'all' 或 'device=0,1'",
        status_code=400,
        tool_id=TOOL_ID,
    )


def _extract_volumes_from_raw_cmd(cmd: str) -> list[str]:
    """从 docker run 原始命令中提取 -v / --volume 挂载路径。

    返回宿主机侧路径列表（如 ['/host/path', '/data']）。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return []
    volumes: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-v", "--volume"):
            if i + 1 < len(tokens):
                vol_spec = tokens[i + 1]
                # -v /host:/container 或 -v /host:/container:ro
                host_part = vol_spec.split(":")[0]
                volumes.append(host_part)
                i += 2
                continue
        elif tok.startswith("-v") and len(tok) > 2:
            # -v/host:/container 格式（连写）
            vol_spec = tok[2:]
            host_part = vol_spec.split(":")[0]
            volumes.append(host_part)
        elif tok.startswith("--volume="):
            vol_spec = tok[len("--volume="):]
            host_part = vol_spec.split(":")[0]
            volumes.append(host_part)
        i += 1
    return volumes


def _extract_volume_specs_from_raw_cmd(cmd: str) -> list[str]:
    """Extract raw -v/--volume specs from a docker run command."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return []
    specs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-v", "--volume"):
            if i + 1 < len(tokens):
                specs.append(tokens[i + 1])
                i += 2
                continue
        elif tok.startswith("-v") and len(tok) > 2:
            specs.append(tok[2:])
        elif tok.startswith("--volume="):
            specs.append(tok[len("--volume="):])
        elif tok in ("--mount",):
            if i + 1 < len(tokens):
                spec = _volume_spec_from_mount_option(tokens[i + 1])
                if spec:
                    specs.append(spec)
                i += 2
                continue
        elif tok.startswith("--mount="):
            spec = _volume_spec_from_mount_option(tok[len("--mount="):])
            if spec:
                specs.append(spec)
        i += 1
    return specs


def _volume_spec_from_mount_option(raw: str) -> str:
    """Convert Docker --mount syntax into a simplified source:target style spec."""
    parts: dict[str, str] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    mount_type = parts.get("type", "")
    source = parts.get("source") or parts.get("src") or ""
    target = parts.get("target") or parts.get("dst") or parts.get("destination") or ""
    if mount_type == "bind":
        return f"{source}:{target}" if target else source
    if mount_type == "volume":
        return f"{source}:{target}" if source and target else source
    return ""


def _extract_gpus_from_raw_cmd(cmd: str) -> str:
    """从 docker run 原始命令中提取 --gpus 参数值。

    返回 gpus 参数值（如 'all' 或 'device=0,1'），未找到则返回空字符串。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--gpus":
            if i + 1 < len(tokens):
                return tokens[i + 1]
            i += 1
        elif tok.startswith("--gpus="):
            return tok[len("--gpus="):]
        i += 1
    return ""


def _extract_image_from_raw_cmd(cmd: str) -> str:
    """Best-effort extraction of the image token from a docker run command."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    if len(tokens) < 3:
        return ""
    i = 2  # docker run
    opts_with_value = {
        "-e", "--env", "--env-file", "-h", "--hostname", "--name", "--user", "-u",
        "-w", "--workdir", "-p", "--publish", "--expose", "-v", "--volume",
        "--mount", "--network", "--restart", "--gpus", "--add-host", "--label",
        "--log-driver", "--log-opt", "--entrypoint", "--cpus", "--memory", "-m",
    }
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            return tokens[i + 1] if i + 1 < len(tokens) else ""
        if not tok.startswith("-"):
            return tok
        if tok in opts_with_value and i + 1 < len(tokens):
            i += 2
            continue
        i += 1
    return ""


def _extract_volumes_from_compose(yaml_content: str) -> list[str]:
    """从 docker-compose YAML 中提取 bind 挂载的宿主机路径。

    仅提取 type: bind 或直接路径格式的挂载，忽略 named volume。
    """
    try:
        import yaml
    except ImportError:
        return []
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    paths: list[str] = []
    services = data.get("services", {})
    if not isinstance(services, dict):
        return paths
    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        vols = svc_config.get("volumes", [])
        if not isinstance(vols, list):
            continue
        for vol in vols:
            if isinstance(vol, str):
                # "/host:/container" 或 "/host:/container:ro"
                parts = vol.split(":")
                if len(parts) >= 2:
                    host_part = parts[0]
                    # 忽略 named volume（不以 / 开头的）
                    if host_part.startswith("/"):
                        paths.append(host_part)
            elif isinstance(vol, dict):
                # {type: bind, source: /host, target: /container}
                if vol.get("type") == "bind":
                    src = vol.get("source", "")
                    if src and src.startswith("/"):
                        paths.append(src)
    return paths


def _extract_container_resources_from_compose(
    yaml_content: str,
    project_name: str = "",
) -> dict[str, list[str]]:
    """Extract images, bind paths, and named volumes from docker compose YAML."""
    try:
        import yaml
    except ImportError:
        return {"images": [], "bindPaths": [], "volumeSpecs": []}
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return {"images": [], "bindPaths": [], "volumeSpecs": []}
    if not isinstance(data, dict):
        return {"images": [], "bindPaths": [], "volumeSpecs": []}

    top_volumes = data.get("volumes", {})
    external_volumes: set[str] = set()
    if isinstance(top_volumes, dict):
        for name, config in top_volumes.items():
            if isinstance(config, dict) and config.get("external"):
                external_volumes.add(str(config.get("name") or name))

    images: list[str] = []
    bind_paths: list[str] = []
    volume_specs: list[str] = []
    services = data.get("services", {})
    if not isinstance(services, dict):
        return {"images": [], "bindPaths": [], "volumeSpecs": []}

    def _compose_volume_ref(source: str) -> str:
        if source in external_volumes:
            return source
        if project_name and source:
            return f"{project_name}_{source}"
        return source

    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        image_ref = svc_config.get("image")
        if isinstance(image_ref, str) and image_ref.strip():
            images.append(image_ref.strip())
        vols = svc_config.get("volumes", [])
        if not isinstance(vols, list):
            continue
        for vol in vols:
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    source = parts[0]
                    if source.startswith("/") or source.startswith(".") or source.startswith("~"):
                        bind_paths.append(source)
                    elif source:
                        volume_specs.append(f"{_compose_volume_ref(source)}:{parts[1]}")
                elif len(parts) == 1 and parts[0].startswith("/"):
                    volume_specs.append(parts[0])
            elif isinstance(vol, dict):
                mount_type = vol.get("type")
                source = str(vol.get("source") or vol.get("src") or "")
                target = str(vol.get("target") or vol.get("dst") or vol.get("destination") or "")
                if mount_type == "bind":
                    if source:
                        bind_paths.append(source)
                elif mount_type == "volume":
                    if source:
                        volume_specs.append(f"{_compose_volume_ref(source)}:{target}" if target else _compose_volume_ref(source))
                    elif target:
                        volume_specs.append(target)

    return {
        "images": list(dict.fromkeys(images)),
        "bindPaths": list(dict.fromkeys(bind_paths)),
        "volumeSpecs": list(dict.fromkeys(volume_specs)),
    }


def _compose_has_build(yaml_content: str) -> bool:
    try:
        import yaml
    except ImportError:
        return False
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    services = data.get("services", {})
    if not isinstance(services, dict):
        return False
    return any(isinstance(config, dict) and bool(config.get("build")) for config in services.values())


def list_containers(server_id: str, user: User, all_containers: bool = True) -> list[dict[str, Any]]:
    """列出服务器上的容器，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        if user.role != "admin" and not perms.get("ctr_use") and not perms.get("ctr_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看容器的权限", status_code=403, tool_id=TOOL_ID)
        # 获取容器所有权元数据
        meta_rows = conn.execute(
            "SELECT container_ref, owner_user_id FROM docker_containers_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map: dict[str, str] = {r["container_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的所有容器 ref。creator/quota_holder 不具备查看权
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
            (server_id, user.id),
        ).fetchall():
            user_accessible_refs.add(r["resource_ref"])
        view_all = user.role == "admin" or perms.get("ctr_view_all", False)
        cache_rows = conn.execute(
            "SELECT * FROM docker_df_containers WHERE server_id=? ORDER BY names",
            (server_id,),
        ).fetchall()

    containers = []
    cache_updates: list[tuple[str, str, str, str]] = []
    now_str = _now()
    for cache in cache_rows:
        status = cache["status"] or ""
        if not all_containers and not status.lower().startswith("up"):
            continue
        ctr_name = cache["names"] or cache["container_id"][:12]
        ctr_id_short = cache["container_id"][:12]
        ref = ctr_name or ctr_id_short
        owner = meta_map.get(ctr_name) or meta_map.get(ctr_id_short)
        ctr = {
            "ID": ctr_id_short,
            "Names": ctr_name,
            "Image": cache["image"],
            "Command": cache["command"],
            "Status": status,
            "State": status.split(" ", 1)[0].lower() if status else "",
            "Ports": cache["ports"],
            "CreatedAt": cache["created"],
            "Size": cache["size"],
            "LocalVolumes": cache["local_volumes"],
            "ownerUserId": owner,
            "platformManaged": owner is not None or ref in user_accessible_refs,
        }

        if ref and cache["image"]:
            cache_updates.append((ref, "image", cache["image"], now_str))

        if view_all:
            containers.append(ctr)
        elif ref in user_accessible_refs or ctr_id_short in user_accessible_refs or owner == user.id:
            containers.append(ctr)

    if cache_updates:
        with get_connection() as conn:
            for container_ref, rtype, rref, ts in cache_updates:
                conn.execute(
                    """INSERT OR REPLACE INTO docker_container_resource_cache
                       (server_id, container_ref, resource_type, resource_ref, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (server_id, container_ref, rtype, rref, ts),
                )
                if rtype == "image":
                    conn.execute(
                        """INSERT OR REPLACE INTO docker_container_resource_cache
                           (server_id, container_ref, resource_type, resource_ref, updated_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (server_id, container_ref, rtype, _normalize_image_ref(rref), ts),
                    )

    return containers


def _validate_path_whitelist(mount_paths: list[str], whitelist: list[str]) -> None:
    """校验挂载路径是否在白名单内（前缀匹配）"""
    for path in mount_paths:
        host_path = path.split(":")[0] if ":" in path else path
        # 仅限制宿主机绝对路径 bind mount；named volume 不属于路径白名单范畴。
        if not host_path.startswith("/"):
            continue
        if not whitelist:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"未配置宿主机路径挂载白名单，禁止挂载 {host_path}",
                status_code=403,
                tool_id=TOOL_ID,
            )
        allowed = any(host_path.startswith(w.rstrip("/")) for w in whitelist)
        if not allowed:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"路径 {host_path} 不在允许的挂载白名单中",
                status_code=403,
                tool_id=TOOL_ID,
            )


def _is_bind_mount_source(source: str) -> bool:
    return source.startswith("/") or source.startswith(".") or source.startswith("~")


def _volume_source_from_spec(spec: str) -> tuple[str, str]:
    """Return (kind, source) where kind is bind, named, anonymous, or none."""
    raw = spec.strip()
    if not raw:
        return "none", ""
    parts = raw.split(":")
    if len(parts) == 1:
        return ("anonymous", "") if parts[0].startswith("/") else ("named", parts[0])
    source = parts[0].strip()
    if not source:
        return "anonymous", ""
    if _is_bind_mount_source(source):
        return "bind", source
    return "named", source


def _remote_docker_resource_exists(server_row: sqlite3.Row, resource_type: str, resource_ref: str) -> bool:
    cmd = ""
    if resource_type == "image":
        cmd = f"docker image inspect {shlex.quote(resource_ref)} >/dev/null 2>&1"
    elif resource_type == "volume":
        cmd = f"docker volume inspect {shlex.quote(resource_ref)} >/dev/null 2>&1"
    else:
        return False
    client = _ssh_connect(server_row)
    try:
        _, _, rc = _ssh_exec(client, cmd, timeout=20)
    finally:
        client.close()
    return rc == 0


def _plan_container_image_usage(
    conn: sqlite3.Connection,
    server_row: sqlite3.Row,
    image_refs: list[str],
    user: User,
) -> list[str]:
    """Validate images used by a new container and return auto-pulled refs to record after success."""
    normalized_refs: list[str] = []
    for image_ref in [ref for ref in image_refs if ref.strip()]:
        normalized_ref = _normalize_image_ref(image_ref)
        if normalized_ref.startswith("__") and normalized_ref.endswith("__"):
            raise ToolboxError("INVALID_IMAGE", f"无效的镜像名称: {normalized_ref}", status_code=400, tool_id=TOOL_ID)
        normalized_refs.append(normalized_ref)
    if user.role == "admin":
        return []
    perms = _get_user_perms(conn, server_row["id"], user)
    can_use_images = bool(perms.get("img_use") or perms.get("img_view_all") or perms.get("img_manage_all"))
    pulled_refs: list[str] = []
    for normalized_ref in normalized_refs:
        exists = _remote_docker_resource_exists(server_row, "image", normalized_ref)
        can_access_existing = (
            perms.get("img_view_all")
            or perms.get("img_manage_all")
            or _user_can_access_resource(conn, server_row["id"], "image", normalized_ref, user)
        )
        if exists:
            if not can_use_images:
                raise ToolboxError("PERMISSION_DENIED", "您没有使用镜像的权限，无法创建容器", status_code=403, tool_id=TOOL_ID)
            if not can_access_existing:
                raise ToolboxError("PERMISSION_DENIED", f"您没有访问镜像 {normalized_ref} 的权限，无法创建容器", status_code=403, tool_id=TOOL_ID)
            continue
        if not (perms.get("img_use") and perms.get("img_pull")):
            raise ToolboxError(
                "PERMISSION_DENIED",
                f"镜像 {normalized_ref} 不存在或不可见，且您没有拉取并使用镜像的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        _enforce_image_quota(conn, server_row, user)
        pulled_refs.append(normalized_ref)
    return list(dict.fromkeys(pulled_refs))


def _plan_container_volume_usage(
    conn: sqlite3.Connection,
    server_row: sqlite3.Row,
    volume_specs: list[str],
    user: User,
) -> list[str]:
    """Validate named/anonymous Docker volumes and return newly created named volumes to record."""
    volume_sources = [_volume_source_from_spec(spec) for spec in volume_specs]
    for kind, source in volume_sources:
        if kind not in {"none", "bind"} and source.startswith("__") and source.endswith("__"):
            raise ToolboxError("INVALID_VOLUME", f"无效的卷名称: {source}", status_code=400, tool_id=TOOL_ID)
    if user.role == "admin":
        return []
    perms = _get_user_perms(conn, server_row["id"], user)
    created_named_volumes: list[str] = []
    for kind, source in volume_sources:
        if kind in {"none", "bind"}:
            continue
        if kind == "anonymous":
            if not perms.get("vol_create"):
                raise ToolboxError("PERMISSION_DENIED", "匿名卷会创建新 Docker 卷，您没有创建卷的权限", status_code=403, tool_id=TOOL_ID)
            _enforce_volume_quota(conn, server_row, user)
            continue

        exists = _remote_docker_resource_exists(server_row, "volume", source)
        can_access_existing = (
            perms.get("vol_view_all")
            or perms.get("vol_manage_all")
            or _user_can_access_resource(conn, server_row["id"], "volume", source, user)
        )
        if exists:
            if not perms.get("vol_use") and not perms.get("vol_view_all") and not perms.get("vol_manage_all"):
                raise ToolboxError("PERMISSION_DENIED", "您没有使用卷的权限，无法挂载 Docker 卷", status_code=403, tool_id=TOOL_ID)
            if not can_access_existing:
                raise ToolboxError("PERMISSION_DENIED", f"您没有访问卷 {source} 的权限，无法挂载到容器", status_code=403, tool_id=TOOL_ID)
            continue

        if not perms.get("vol_create"):
            raise ToolboxError("PERMISSION_DENIED", f"卷 {source} 不存在或不可见，且您没有创建卷的权限", status_code=403, tool_id=TOOL_ID)
        _enforce_volume_quota(conn, server_row, user)
        created_named_volumes.append(source)
    return list(dict.fromkeys(created_named_volumes))


def _record_container_resource_creations(
    server_id: str,
    user: User,
    image_refs: list[str],
    volume_refs: list[str],
) -> None:
    if user.role == "admin":
        return
    with get_connection() as conn:
        for image_ref in image_refs:
            _record_resource_creator(conn, server_id, "image", _normalize_image_ref(image_ref), user.id)
        for volume_ref in volume_refs:
            _record_resource_creator(conn, server_id, "volume", volume_ref, user.id)


def create_container_run(server_id: str, params: dict[str, Any], user: User) -> dict[str, Any]:
    """
    通过 docker run 参数创建容器。
    params 结构：
    {
        "name": str,
        "image": str,
        "command": str,
        "ports": ["8080:80", ...],
        "volumes": ["/host:/container", ...],
        "envs": ["KEY=VALUE", ...],
        "network": str,
        "restart": str,
        "gpus": str,   // e.g. "all" or ""
        "extra_args": str   // 额外原始参数字符串
    }
    """
    init_docker_database()
    image_ref = (params.get("image") or "").strip()
    if not image_ref:
        raise ToolboxError("INVALID_IMAGE", "镜像不能为空", status_code=400, tool_id=TOOL_ID)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单
            volumes = params.get("volumes", [])
            if volumes:
                whitelist = perms.get("ctr_path_whitelist", [])
                _validate_path_whitelist(volumes, whitelist)
            # 校验 GPU 权限
            gpus_arg = params.get("gpus", "") or ""
            if gpus_arg:
                _validate_gpus_permission(gpus_arg, perms.get("cuda_gpu_indices", []))
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
        images_to_record = _plan_container_image_usage(conn, row, [image_ref], user)
        volumes_to_record = _plan_container_volume_usage(conn, row, params.get("volumes", []), user)

    # 构建 docker run 命令
    cmd_parts = ["docker", "run", "-d"]

    if params.get("name"):
        cmd_parts += ["--name", shlex.quote(params["name"])]

    if params.get("restart"):
        cmd_parts += ["--restart", shlex.quote(params["restart"])]

    for port in params.get("ports", []):
        cmd_parts += ["-p", shlex.quote(port)]

    for vol in params.get("volumes", []):
        cmd_parts += ["-v", shlex.quote(vol)]

    for env in params.get("envs", []):
        cmd_parts += ["-e", shlex.quote(env)]

    if params.get("network"):
        cmd_parts += ["--network", shlex.quote(params["network"])]

    if params.get("gpus"):
        cmd_parts += ["--gpus", shlex.quote(params["gpus"])]

    if params.get("extra_args"):
        # 安全追加额外参数
        cmd_parts += shlex.split(params["extra_args"])

    cmd_parts.append(shlex.quote(image_ref))

    if params.get("command"):
        cmd_parts += shlex.split(params["command"])

    full_cmd = " ".join(cmd_parts)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, full_cmd, timeout=60)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CREATE_CONTAINER_FAILED", f"创建容器失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )

    # 记录容器所有权（创建者=所有者=配额占用者）
    # docker run -d 输出容器长 ID，取前12位作为短 ID；同时用容器名（若有）作为 ref
    container_id_full = stdout.strip()
    container_id_short = container_id_full[:12]
    container_name = params.get("name", "") or ""
    with get_connection() as conn:
        # 用容器名和短 ID 都记录所有权，确保 list_containers 的双重匹配能命中
        if container_name:
            _record_resource_creator(conn, server_id, "container", container_name, user.id)
        _record_resource_creator(conn, server_id, "container", container_id_short, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "containerId": container_id_full, "command": full_cmd}


def create_container_run_raw(server_id: str, command: str, user: User) -> dict[str, Any]:
    """
    直接在服务器上执行用户提供的完整 docker run 命令（命令行模式）。
    安全校验：
    - 命令必须以 `docker run` 开头（大小写不敏感、允许前缀空格）
    - 用户必须拥有创建容器的权限 + 服务器可见性
    - 校验挂载路径白名单（从命令中解析 -v/--volume）
    - 校验 GPU 权限（从命令中解析 --gpus）
    - 容器数量配额校验
    """
    init_docker_database()
    cmd = command.strip()
    # 安全性：只允许 docker run 命令
    if not cmd.lower().startswith("docker run"):
        raise ToolboxError("INVALID_COMMAND", "命令必须以 'docker run' 开头", status_code=400, tool_id=TOOL_ID)

    raw_image = _extract_image_from_raw_cmd(cmd)
    if not raw_image:
        raise ToolboxError("INVALID_IMAGE", "无法从 docker run 命令中解析镜像", status_code=400, tool_id=TOOL_ID)
    raw_volume_specs = _extract_volume_specs_from_raw_cmd(cmd)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单（从原始命令中解析）
            whitelist = perms.get("ctr_path_whitelist", [])
            if raw_volume_specs:
                _validate_path_whitelist(raw_volume_specs, whitelist)
            # 校验 GPU 权限
            raw_gpus = _extract_gpus_from_raw_cmd(cmd)
            if raw_gpus:
                _validate_gpus_permission(raw_gpus, perms.get("cuda_gpu_indices", []))
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
        images_to_record = _plan_container_image_usage(conn, row, [raw_image], user)
        volumes_to_record = _plan_container_volume_usage(conn, row, raw_volume_specs, user)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=120)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CREATE_CONTAINER_FAILED", f"创建容器失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )

    # 记录容器所有权
    # 尝试从命令中解析容器名；docker run -d 输出容器长 ID
    container_id_full = stdout.strip()
    container_id_short = container_id_full[:12]
    # 从命令中解析 --name 参数
    container_name = ""
    try:
        tokens = shlex.split(cmd)
        for i, tok in enumerate(tokens):
            if tok == "--name" and i + 1 < len(tokens):
                container_name = tokens[i + 1]
                break
            elif tok.startswith("--name="):
                container_name = tok[len("--name="):]
                break
    except ValueError:
        pass
    with get_connection() as conn:
        if container_name:
            _record_resource_creator(conn, server_id, "container", container_name, user.id)
        _record_resource_creator(conn, server_id, "container", container_id_short, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "containerId": container_id_full, "command": cmd}


def create_container_compose(server_id: str, yaml_content: str, user: User, project_name: str = "") -> dict[str, Any]:
    """
    通过 docker compose 创建容器：将 YAML 上传至服务器临时目录执行。
    安全校验：server_visible + ctr_create + 路径白名单 + 容器配额。
    """
    init_docker_database()
    compose_resources = _extract_container_resources_from_compose(yaml_content, project_name)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单（从 YAML 中解析 volumes 的 bind 挂载）
            whitelist = perms.get("ctr_path_whitelist", [])
            if compose_resources["bindPaths"]:
                _validate_path_whitelist(compose_resources["bindPaths"], whitelist)
            if _compose_has_build(yaml_content) and not (perms.get("img_use") and perms.get("img_pull")):
                raise ToolboxError("PERMISSION_DENIED", "Compose build 会创建镜像，您没有使用并拉取/新增镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
        images_to_record = _plan_container_image_usage(conn, row, compose_resources["images"], user)
        volumes_to_record = _plan_container_volume_usage(conn, row, compose_resources["volumeSpecs"], user)

    tmp_dir = f"/tmp/.docker_manager_{uuid.uuid4().hex[:8]}"
    compose_file = f"{tmp_dir}/docker-compose.yml"

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    client = _ssh_connect(row)
    try:
        sftp = client.open_sftp()
        try:
            client.exec_command(f"mkdir -p {shlex.quote(tmp_dir)}")[1].channel.recv_exit_status()
            with sftp.file(compose_file, "w") as f:
                f.write(yaml_content)
        finally:
            sftp.close()

        project_flag = f"-p {shlex.quote(project_name)}" if project_name else ""
        cmd = f"docker compose {project_flag} -f {shlex.quote(compose_file)} up -d"
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=300)

        # 无论成功与否，清理临时目录
        client.exec_command(f"rm -rf {shlex.quote(tmp_dir)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "COMPOSE_FAILED", f"docker compose 失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )

    # 记录 compose 创建的容器所有权
    # 通过 docker compose ps 获取该 project 下所有容器
    ctr_refs: list[str] = []
    record_client = _ssh_connect(row)
    try:
        # compose_file 已被删除，只能用 project name 查询
        if project_name:
            ps_cmd = f"docker compose -p {shlex.quote(project_name)} ps --format '{{{{json .}}}}'"
            ps_out, _, ps_rc = _ssh_exec(record_client, ps_cmd, timeout=30)
            if ps_rc == 0:
                for line in ps_out.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ctr_info = json.loads(line)
                        # docker compose ps 的 Name 字段是容器名
                        name = ctr_info.get("Name", "") or ctr_info.get("Names", "")
                        if name:
                            ctr_refs.append(name.lstrip("/"))
                    except json.JSONDecodeError:
                        continue
    finally:
        record_client.close()

    if ctr_refs:
        with get_connection() as conn:
            for ref in ctr_refs:
                _record_resource_creator(conn, server_id, "container", ref, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "output": stdout + stderr, "projectName": project_name}


def container_action(server_id: str, container_id: str, action: str, user: User) -> dict[str, Any]:
    """
    容器生命周期操作：start/stop/restart/remove
    需要是容器的所有者（owner 角色）或拥有 ctr_manage_all 权限
    """
    init_docker_database()
    if action not in {"start", "stop", "restart", "remove"}:
        raise ToolboxError("INVALID_ACTION", "无效的容器操作", status_code=400, tool_id=TOOL_ID)

    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            # 新模型：检查用户是否是该容器的 owner（creator 不拥有管理权）
            is_resource_owner = _user_can_manage_resource(conn, server_id, "container", container_id, user)
            if not can_manage_all and not is_resource_owner:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限（需要是容器所有者或拥有全局管理权限）", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    docker_cmd = "rm -f" if action == "remove" else action
    cmd = f"docker {docker_cmd} {shlex.quote(container_id)}"
    cleanup_refs = _resource_ref_candidates("container", container_id)

    client = _ssh_connect(row)
    try:
        if action == "remove":
            inspect_out, _, inspect_code = _ssh_exec(
                client,
                f"docker inspect --format '{{{{.Name}}}}\t{{{{.Id}}}}' {shlex.quote(container_id)}",
                timeout=15,
            )
            if inspect_code == 0 and inspect_out.strip():
                parts = inspect_out.strip().split("\t")
                if parts:
                    cleanup_refs.extend(_resource_ref_candidates("container", parts[0]))
                if len(parts) > 1:
                    cleanup_refs.extend(_resource_ref_candidates("container", parts[1]))
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=60)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CONTAINER_ACTION_FAILED",
            f"容器操作 {action} 失败: {stderr.strip()}",
            status_code=502,
            tool_id=TOOL_ID,
        )

    # 删除容器时清理平台元数据
    if action == "remove":
        with get_connection() as conn:
            _delete_resource_metadata(conn, server_id, "container", cleanup_refs)

    _refresh_docker_df_cache_best_effort(server_id)
    return {"success": True, "action": action, "containerId": container_id}


def update_restart_policy(server_id: str, container_id: str, policy: str, user: User) -> dict[str, Any]:
    """
    更新容器重启策略（docker update --restart）。
    支持：no / always / unless-stopped / on-failure / on-failure:N
    权限：容器所有者（owner 角色）或 ctr_manage_all / admin（任意容器）。
    """
    allowed = {"no", "always", "unless-stopped", "on-failure"}
    base = policy.split(":")[0] if ":" in policy else policy
    if base not in allowed:
        raise ToolboxError("INVALID_POLICY", "无效的重启策略", status_code=400, tool_id=TOOL_ID)

    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            is_resource_owner = _user_can_manage_resource(conn, server_id, "container", container_id, user)
            if not can_manage_all and not is_resource_owner:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    cmd = f"docker update --restart {shlex.quote(policy)} {shlex.quote(container_id)}"
    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=30)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "UPDATE_RESTART_FAILED",
            f"重启策略更新失败: {stderr.strip()}",
            status_code=502,
            tool_id=TOOL_ID,
        )
    return {"success": True, "restartPolicy": policy}


def get_container_detail(server_id: str, container_id: str, user: User) -> dict[str, Any]:
    """
    获取容器详情（docker inspect），返回基础信息、环境变量、端口映射、卷挂载、网络等。
    权限：容器的 owner/viewer 角色，或 ctr_view_all / admin（所有容器）。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            ctr_use = perms.get("ctr_use", False)
            if not view_all and not ctr_use:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器详情的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                # 检查是否有该容器的查看角色（owner/viewer）
                if not _user_can_access_resource(conn, server_id, "container", container_id, user):
                    raise ToolboxError("PERMISSION_DENIED", "您没有权限查看此容器的详情", status_code=403, tool_id=TOOL_ID)

        # 查询平台元数据
        meta_row = conn.execute(
            "SELECT owner_user_id, assigned_at FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
            (server_id, container_id),
        ).fetchone()
        # 查询显示端口配置（从 display_ports 列，若存在）
        display_port_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_containers_meta)").fetchall()}
        display_ports_raw = None
        if "display_ports" in display_port_cols:
            dp_row = conn.execute(
                "SELECT display_ports FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                (server_id, container_id),
            ).fetchone()
            if dp_row:
                display_ports_raw = dp_row["display_ports"]

        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            f"docker inspect {shlex.quote(container_id)}",
            timeout=30,
        )
    finally:
        client.close()

    if code != 0 or not stdout.strip():
        raise ToolboxError("INSPECT_FAILED", f"获取容器详情失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    try:
        inspect_list = json.loads(stdout.strip())
        if not inspect_list:
            raise ToolboxError("NOT_FOUND", "容器不存在", status_code=404, tool_id=TOOL_ID)
        d = inspect_list[0]
    except (json.JSONDecodeError, IndexError) as exc:
        raise ToolboxError("PARSE_ERROR", "解析 docker inspect 输出失败", status_code=502, tool_id=TOOL_ID) from exc

    cfg = d.get("Config", {})
    host_cfg = d.get("HostConfig", {})
    net_settings = d.get("NetworkSettings", {})
    state = d.get("State", {})
    mounts = d.get("Mounts", [])

    # 端口映射
    port_bindings = host_cfg.get("PortBindings") or {}
    ports_list: list[dict[str, Any]] = []
    for container_port, bindings in port_bindings.items():
        if bindings:
            for b in bindings:
                ports_list.append({
                    "containerPort": container_port,
                    "hostIp": b.get("HostIp", ""),
                    "hostPort": b.get("HostPort", ""),
                })
        else:
            ports_list.append({"containerPort": container_port, "hostIp": "", "hostPort": ""})

    # 卷挂载
    mounts_list: list[dict[str, Any]] = []
    for m in mounts:
        mounts_list.append({
            "type": m.get("Type", ""),
            "source": m.get("Source", ""),
            "destination": m.get("Destination", ""),
            "mode": m.get("Mode", ""),
            "rw": m.get("RW", True),
            "name": m.get("Name", ""),
        })

    # 网络
    networks: list[dict[str, Any]] = []
    for net_name, net_info in (net_settings.get("Networks") or {}).items():
        networks.append({
            "name": net_name,
            "ipAddress": net_info.get("IPAddress", ""),
            "gateway": net_info.get("Gateway", ""),
            "macAddress": net_info.get("MacAddress", ""),
        })

    # SSH 端口预留：从端口映射中查找宿主机侧映射到容器 22 端口的
    ssh_host_port: str | None = None
    for pb in ports_list:
        cp = pb.get("containerPort", "")
        if cp in ("22/tcp", "22"):
            hp = pb.get("hostPort", "")
            if hp:
                ssh_host_port = hp
                break

    return {
        "id": d.get("Id", ""),
        "shortId": d.get("Id", "")[:12],
        "name": d.get("Name", "").lstrip("/"),
        "image": cfg.get("Image", ""),
        "imageId": d.get("Image", ""),
        "status": state.get("Status", ""),
        "running": state.get("Running", False),
        "paused": state.get("Paused", False),
        "restarting": state.get("Restarting", False),
        "startedAt": state.get("StartedAt", ""),
        "finishedAt": state.get("FinishedAt", ""),
        "exitCode": state.get("ExitCode", 0),
        "created": d.get("Created", ""),
        "restartPolicy": host_cfg.get("RestartPolicy", {}).get("Name", ""),
        "platform": d.get("Platform", ""),
        "hostname": cfg.get("Hostname", ""),
        "cmd": cfg.get("Cmd") or [],
        "entrypoint": cfg.get("Entrypoint") or [],
        "workingDir": cfg.get("WorkingDir", ""),
        "user": cfg.get("User", ""),
        "envs": cfg.get("Env") or [],
        "ports": ports_list,
        "mounts": mounts_list,
        "networks": networks,
        "sshHostPort": ssh_host_port,
        "serverHost": row["host"],
        "serverSshUsername": row["ssh_username"],
        "platformMeta": {
            "ownerUserId": meta_row["owner_user_id"] if meta_row else None,
            "assignedAt": meta_row["assigned_at"] if meta_row else None,
            "displayPorts": json.loads(display_ports_raw) if display_ports_raw else None,
        },
    }


def get_container_logs(server_id: str, container_id: str, user: User, tail: int = 200) -> dict[str, Any]:
    """获取容器日志（需容器 owner/viewer 角色或 ctr_view_all）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            ctr_use = perms.get("ctr_use", False)
            if not view_all and not ctr_use:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器日志的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                if not _user_can_access_resource(conn, server_id, "container", container_id, user):
                    raise ToolboxError("PERMISSION_DENIED", "您只能查看自己有权限的容器日志", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            f"docker logs --tail {tail} {shlex.quote(container_id)} 2>&1",
            timeout=30,
        )
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("LOGS_FAILED", f"获取日志失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)
    return {"logs": stdout, "containerId": container_id}


