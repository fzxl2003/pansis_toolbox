from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})
from .containers import create_container_run, create_container_run_raw, create_container_compose
import re as _re

# ==============================================================
# 模板管理（支持占位符变量 + 多角色权限）
# ==============================================================

# 占位符正则：匹配 {{VAR_NAME}} 格式
# 变量名允许字母、数字、下划线，至少 1 个字符
_PLACEHOLDER_RE = _re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

# 支持的变量类型
_VARIABLE_TYPES = {"string", "text", "number", "port", "image", "volume", "select", "gpu", "host_path", "docker_path"}

# 需要从服务器预定义选项中筛选的类型（筛选条件 = 通配符，用于过滤下拉选项）
_OPTION_FILTER_TYPES = {"image", "volume", "gpu"}
# 文本类类型（筛选条件 = 通配符，用户输入必须匹配）
_TEXT_FILTER_TYPES = {"string", "text", "docker_path"}
# 路径类类型（host_path 筛选条件为通配符前缀约束，如 /data/*；docker_path 走文本通配符校验）
_PATH_FILTER_TYPES = {"host_path"}
# 数字类类型（筛选条件 = 范围约束，用户输入必须满足）
_NUMBER_FILTER_TYPES = {"number", "port"}


def _normalize_variable(var: Any) -> dict[str, Any]:
    """规范化变量声明：
    - 向后兼容：旧字段 `matcher` 迁移为 `filter`（筛选条件）
    - 确保 filter / type / name / description / defaultValue 字段存在
    - 校正非法 type
    """
    if not isinstance(var, dict):
        return {"name": "", "type": "string", "filter": "", "description": "", "defaultValue": ""}
    # 向后兼容：matcher → filter
    if "filter" not in var and "matcher" in var:
        var["filter"] = var.pop("matcher")
    var.setdefault("filter", "")
    if var.get("type", "string") not in _VARIABLE_TYPES:
        var["type"] = "string"
    var.setdefault("name", "")
    var.setdefault("description", "")
    var.setdefault("defaultValue", "")
    return var


def _wildcard_to_regex(pattern: str) -> _re.Pattern:
    """将通配符模式转为正则。

    支持：
      *  任意字符序列
      ?  单个任意字符
      |  多模式 OR（任一匹配即可）
    大小写不敏感。其余字符按字面量匹配（自动转义正则特殊字符）。
    """
    alternatives = (pattern or "").split("|")
    parts: list[str] = []
    for alt in alternatives:
        sub = ""
        for ch in alt:
            if ch == "*":
                sub += ".*"
            elif ch == "?":
                sub += "."
            else:
                sub += _re.escape(ch)
        parts.append(sub)
    return _re.compile("(?:" + "|".join(parts) + ")", _re.IGNORECASE)


def _matches_wildcard(value: str, pattern: str) -> bool:
    """判断 value 是否完整匹配通配符 pattern。空 pattern 视为匹配所有。"""
    if not pattern or not pattern.strip():
        return True
    return _wildcard_to_regex(pattern).fullmatch(value or "") is not None


def _parse_number_range(filter_str: str) -> dict[str, Any] | None:
    """解析数字筛选条件，返回约束 dict。

    支持形式（逗号分隔，全部需同时满足 = AND）：
      a-b        → a <= x <= b（闭区间）
      >=a  >a    → 下界（闭/开）
      <=b  <b    → 上界（闭/开）
      =a         → 必须等于 a
    返回 {'min': float|None, 'max': float|None, 'min_excl': bool, 'max_excl': bool, 'eq': float|None}
    若解析失败（含无法识别的片段）返回 None。
    """
    if not filter_str or not filter_str.strip():
        return None
    result: dict[str, Any] = {"min": None, "max": None, "min_excl": False, "max_excl": False, "eq": None}
    parts = [p.strip() for p in filter_str.split(",") if p.strip()]
    for p in parts:
        m = _re.match(r'^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["min"] = float(m.group(1))
            result["max"] = float(m.group(2))
            continue
        m = _re.match(r'^>=\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["min"] = float(m.group(1)); result["min_excl"] = False; continue
        m = _re.match(r'^>\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["min"] = float(m.group(1)); result["min_excl"] = True; continue
        m = _re.match(r'^<=\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["max"] = float(m.group(1)); result["max_excl"] = False; continue
        m = _re.match(r'^<\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["max"] = float(m.group(1)); result["max_excl"] = True; continue
        m = _re.match(r'^=\s*(-?\d+(?:\.\d+)?)$', p)
        if m:
            result["eq"] = float(m.group(1)); continue
        return None  # 含无法识别的片段
    return result


def _validate_number_value(value: str, filter_str: str, is_port: bool) -> tuple[bool, str]:
    """校验数字/端口值，返回 (是否合法, 错误信息)。空值视为合法（允许留空）。"""
    if value is None or value == "":
        return True, ""
    try:
        num = float(value)
    except (ValueError, TypeError):
        return False, f"不是合法的数字：{value!r}"
    if is_port:
        if num != int(num) or num < 1 or num > 65535:
            return False, "端口号必须为 1-65535 的整数"
        num = int(num)
    rng = _parse_number_range(filter_str)
    if rng:
        if rng["eq"] is not None and num != rng["eq"]:
            return False, f"数值必须等于 {rng['eq']:g}"
        if rng["min"] is not None:
            if rng["min_excl"] and not (num > rng["min"]):
                return False, f"数值必须大于 {rng['min']:g}"
            if not rng["min_excl"] and not (num >= rng["min"]):
                return False, f"数值必须大于等于 {rng['min']:g}"
        if rng["max"] is not None:
            if rng["max_excl"] and not (num < rng["max"]):
                return False, f"数值必须小于 {rng['max']:g}"
            if not rng["max_excl"] and not (num <= rng["max"]):
                return False, f"数值必须小于等于 {rng['max']:g}"
    return True, ""


# ============================================================
# 结构化筛选条件（DNF：组间 OR、组内 AND）
# ============================================================

def _is_structured_filter(filter_str: str) -> bool:
    """判断 filter 字符串是否为结构化筛选条件（JSON 格式，以 {"groups": 开头）。"""
    s = (filter_str or "").strip()
    return s.startswith('{"groups":') or s.startswith('{ "groups":')


def _parse_structured_filter(filter_str: str) -> dict[str, Any] | None:
    """解析结构化筛选条件 JSON 字符串。非结构化或解析失败返回 None。"""
    if not _is_structured_filter(filter_str):
        return None
    try:
        obj = json.loads(filter_str)
        if isinstance(obj, dict) and isinstance(obj.get("groups"), list):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _eval_condition(cond: dict[str, Any], value: str, var_type: str) -> bool:
    """对单个结构化条件求值。返回 True 表示该条件满足。"""
    op = cond.get("op", "")
    if op == "match":
        return _matches_wildcard(value, cond.get("pattern", ""))
    if op == "notMatch":
        return not _matches_wildcard(value, cond.get("pattern", ""))
    # 数值类条件
    if var_type in _NUMBER_FILTER_TYPES:
        try:
            num = float(value)
        except (ValueError, TypeError):
            return False
        try:
            if op == ">=":
                return num >= float(cond.get("value", 0))
            elif op == ">":
                return num > float(cond.get("value", 0))
            elif op == "<=":
                return num <= float(cond.get("value", 0))
            elif op == "<":
                return num < float(cond.get("value", 0))
            elif op == "==":
                return num == float(cond.get("value", 0))
            elif op == "between":
                lo = float(cond.get("min", float("-inf")))
                hi = float(cond.get("max", float("inf")))
                return num >= lo and num <= hi
        except (ValueError, TypeError):
            return False
    return True


def _evaluate_structured_filter(
    filter_obj: dict[str, Any], value: str, var_type: str
) -> tuple[bool, str]:
    """对结构化筛选条件求值。组间 OR、组内 AND。

    返回 (是否合法, 错误信息)。空筛选条件视为匹配所有。
    """
    groups = filter_obj.get("groups", [])
    if not groups:
        return True, ""
    for group in groups:
        conditions = group.get("conditions", [])
        if not conditions:
            return True, ""  # 空条件组视为匹配
        all_match = True
        for cond in conditions:
            if not _eval_condition(cond, value, var_type):
                all_match = False
                break
        if all_match:
            return True, ""
    return False, f"值「{value}」不满足筛选条件"


def _validate_variable_value(var: dict[str, Any], value: str) -> tuple[bool, str]:
    """根据变量类型与筛选条件校验用户输入值。

    返回 (是否合法, 错误信息)。空值统一视为合法（允许变量留空）。
    """
    if value is None or value == "":
        return True, ""
    var_name = var.get("name", "")
    var_type = var.get("type", "string")
    filter_str = (var.get("filter", "") or "").strip()

    # 结构化筛选条件优先处理（JSON 格式，组间 OR、组内 AND）
    structured = _parse_structured_filter(filter_str)
    if structured is not None:
        # host_path 仍需检查绝对路径
        if var_type == "host_path" and not value.startswith("/"):
            return False, f"宿主路径必须为绝对路径（以 / 开头），得到「{value}」"
        # gpu 仍需检查索引格式
        if var_type == "gpu":
            if value.strip().lower() == "all":
                return True, ""
            for idx in [s.strip() for s in value.split(",") if s.strip()]:
                if not _re.match(r"^\d+$", idx):
                    return False, f"GPU 索引必须是非负整数或 all，得到「{idx}」"
        # number/port 仍需检查基础合法性
        if var_type in _NUMBER_FILTER_TYPES:
            try:
                num = float(value)
            except (ValueError, TypeError):
                return False, f"不是合法的数字：{value!r}"
            if var_type == "port" and (num != int(num) or num < 1 or num > 65535):
                return False, "端口号必须为 1-65535 的整数"
        # 用结构化求值
        ok, msg = _evaluate_structured_filter(structured, value, var_type)
        if not ok:
            return False, msg
        _ = var_name
        return True, ""

    # 旧文本格式筛选条件
    if var_type in _TEXT_FILTER_TYPES:
        # 文本类：通配符匹配
        if filter_str and not _matches_wildcard(value, filter_str):
            return False, f"值「{value}」不符合筛选条件「{filter_str}」"
    elif var_type in _OPTION_FILTER_TYPES - {"gpu"}:
        # 服务器选项类（image/volume）：通配符匹配值
        if filter_str and not _matches_wildcard(value, filter_str):
            return False, f"值「{value}」不符合筛选条件「{filter_str}」"
    elif var_type == "gpu":
        # GPU 选择：值应为 "all" 或逗号分隔的非负整数索引（如 "0,1,2"）
        if value.strip().lower() == "all":
            return True, ""
        indices = [s.strip() for s in value.split(",") if s.strip()]
        for idx in indices:
            if not _re.match(r"^\d+$", idx):
                return False, f"GPU 索引必须是非负整数或 all，得到「{idx}」"
    elif var_type == "host_path":
        # 宿主路径：必须为绝对路径；筛选条件为通配符前缀约束（如 /data/* 表示仅允许 /data 下路径）
        if not value.startswith("/"):
            return False, f"宿主路径必须为绝对路径（以 / 开头），得到「{value}」"
        if filter_str and not _matches_wildcard(value, filter_str):
            return False, f"路径「{value}」不在允许范围「{filter_str}」内"
    elif var_type in _NUMBER_FILTER_TYPES:
        ok, msg = _validate_number_value(value, filter_str, is_port=(var_type == "port"))
        if not ok:
            return False, msg
    elif var_type == "select":
        options = [s.strip() for s in filter_str.split(",") if s.strip()]
        if options and value not in options:
            return False, f"值「{value}」不在允许的选项中"
    # 其他类型暂不校验
    _ = var_name
    return True, ""


def detect_placeholders(raw_content: str) -> list[dict[str, Any]]:
    """从原始内容中自动检测 {{VAR_NAME}} 占位符，返回默认变量声明列表。

    导入时允许占位符没有声明，此函数用于自动生成默认声明，
    用户后续可在模板配置界面中编辑类型、筛选条件、说明等。
    """
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(raw_content or ""):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return [
        {
            "name": name,
            "type": "string",
            "filter": "",
            "description": "",
            "defaultValue": "",
        }
        for name in seen
    ]


def _user_can_create_templates(conn: sqlite3.Connection, user: User) -> bool:
    """检查用户是否有创建模板的权限（在任意服务器上拥有 tpl_create 权限）"""
    if user.role == "admin":
        return True
    row = conn.execute(
        "SELECT 1 FROM docker_user_perms WHERE user_id=? AND tpl_create=1 LIMIT 1",
        (user.id,),
    ).fetchone()
    return row is not None


def _user_can_edit_templates(conn: sqlite3.Connection, user: User) -> bool:
    """检查用户是否有编辑模板的权限（在任意服务器上拥有 tpl_edit 权限）"""
    if user.role == "admin":
        return True
    row = conn.execute(
        "SELECT 1 FROM docker_user_perms WHERE user_id=? AND tpl_edit=1 LIMIT 1",
        (user.id,),
    ).fetchone()
    return row is not None


def _user_can_use_templates(conn: sqlite3.Connection, user: User) -> bool:
    """检查用户是否有使用模板的权限（在任意服务器上拥有 tpl_use 权限）"""
    if user.role == "admin":
        return True
    row = conn.execute(
        "SELECT 1 FROM docker_user_perms WHERE user_id=? AND tpl_use=1 LIMIT 1",
        (user.id,),
    ).fetchone()
    return row is not None


def _get_template_roles(conn: sqlite3.Connection, template_id: str) -> dict[str, Any]:
    """获取模板的角色信息：ownerUserIds / viewerUserIds / creatorUserId"""
    rows = conn.execute(
        "SELECT user_id, role FROM docker_template_roles WHERE template_id=?",
        (template_id,),
    ).fetchall()
    owner_ids: list[str] = []
    viewer_ids: list[str] = []
    for r in rows:
        if r["role"] == "owner":
            owner_ids.append(r["user_id"])
        elif r["role"] == "viewer":
            viewer_ids.append(r["user_id"])
    creator_id = conn.execute(
        "SELECT creator_id FROM docker_templates WHERE id=?", (template_id,)
    ).fetchone()
    return {
        "ownerUserIds": owner_ids,
        "viewerUserIds": viewer_ids,
        "creatorUserId": creator_id["creator_id"] if creator_id else None,
    }


def _user_can_access_template(conn: sqlite3.Connection, template_id: str, user: User) -> bool:
    """检查用户是否能查看/使用模板：admin / owner / viewer / 公开模板 + tpl_use 权限"""
    if user.role == "admin":
        return True
    row = conn.execute(
        "SELECT is_public FROM docker_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not row:
        return False
    if row["is_public"]:
        return _user_can_use_templates(conn, user)
    # 非公开模板：必须是 owner 或 viewer
    role_row = conn.execute(
        "SELECT 1 FROM docker_template_roles WHERE template_id=? AND user_id=? AND role IN ('owner','viewer') LIMIT 1",
        (template_id, user.id),
    ).fetchone()
    return role_row is not None


def _user_can_manage_template(conn: sqlite3.Connection, template_id: str, user: User) -> bool:
    """检查用户是否能管理模板（编辑/删除/管理查看者）：admin / owner / tpl_edit 权限"""
    if user.role == "admin":
        return True
    # owner 可管理
    role_row = conn.execute(
        "SELECT 1 FROM docker_template_roles WHERE template_id=? AND user_id=? AND role='owner' LIMIT 1",
        (template_id, user.id),
    ).fetchone()
    if role_row:
        return True
    # 有 tpl_edit 权限的用户也可管理（但只能管理自己创建的？不，tpl_edit 是全局编辑权）
    return _user_can_edit_templates(conn, user)


def _record_template_creator(conn: sqlite3.Connection, template_id: str, user_id: str) -> None:
    """记录模板创建者为 owner 角色"""
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO docker_template_roles (id, template_id, user_id, role, assigned_at) VALUES (?,?,?,?,?)",
        (_new_id(), template_id, user_id, "owner", now),
    )


def _row_to_template_dict(row: sqlite3.Row, include_content: bool = False, roles: dict[str, Any] | None = None) -> dict[str, Any]:
    """将数据库行转换为模板字典"""
    variables = [_normalize_variable(v) for v in (json.loads(row["variables_json"]) if row["variables_json"] else [])]
    # 兼容旧模板：如果 raw_content 为空但 config_json 有内容，尝试从 config 中提取
    raw_content = row["raw_content"]
    deploy_type = row["deploy_type"] if "deploy_type" in row.keys() else "run"
    if not raw_content:
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        deploy_type = config.get("type", "run")
        if deploy_type == "compose":
            raw_content = config.get("composeYaml", "")
        else:
            # 旧模板没有 raw_content，从 config 字段重建 docker run 命令
            parts = ["docker", "run", "-d"]
            if config.get("name"):
                parts += ["--name", config["name"]]
            if config.get("restart"):
                parts += ["--restart", config["restart"]]
            for port in config.get("ports", []):
                parts += ["-p", port]
            for vol in config.get("volumes", []):
                parts += ["-v", vol]
            for env in config.get("envs", []):
                parts += ["-e", env]
            if config.get("network"):
                parts += ["--network", config["network"]]
            if config.get("gpus"):
                parts += ["--gpus", config["gpus"]]
            if config.get("image"):
                parts.append(config["image"])
            if config.get("command"):
                parts += config["command"].split()
            raw_content = " ".join(parts)
        # 如果旧模板没有变量声明，从 raw_content 中自动检测
        if not variables:
            variables = detect_placeholders(raw_content)

    result: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "creatorId": row["creator_id"],
        "hasDoc": bool(row["doc_file"]),
        "isPublic": bool(row["is_public"]),
        "deployType": deploy_type,
        "rawContent": raw_content,
        "variables": variables,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    if include_content and row["doc_file"]:
        doc_path = TEMPLATES_DIR / row["doc_file"]
        if doc_path.exists():
            result["docContent"] = doc_path.read_text(encoding="utf-8")
        else:
            result["docContent"] = ""
    elif include_content:
        result["docContent"] = ""
    if roles is not None:
        result["roles"] = roles
    return result


def create_template(payload: dict[str, Any], user: User) -> dict[str, Any]:
    """创建容器模板。

    权限：管理员 或 在任意服务器上拥有 tpl_create 权限的用户。
    创建者自动成为模板的 owner。
    """
    init_docker_database()
    with get_connection() as conn:
        if not _user_can_create_templates(conn, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有创建模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )

    template_id = _new_id()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ToolboxError("INVALID_NAME", "模板名称不能为空", status_code=400, tool_id=TOOL_ID)

    description = payload.get("description", "")
    category = payload.get("category", "general")
    doc_content = payload.get("docContent", "")
    is_public = bool(payload.get("isPublic", True))
    deploy_type = payload.get("deployType", "run")
    if deploy_type not in ("run", "compose"):
        deploy_type = "run"
    raw_content = payload.get("rawContent", "")
    variables = payload.get("variables", [])

    # 规范化变量声明（matcher → filter 等）
    variables = [_normalize_variable(v) for v in variables]

    # 如果未提供 variables，自动检测占位符
    if not variables:
        variables = detect_placeholders(raw_content)
    else:
        # 确保所有占位符都有声明
        detected = detect_placeholders(raw_content)
        declared_names = {v["name"] for v in variables}
        for d in detected:
            if d["name"] not in declared_names:
                variables.append(d)

    # 保存 MD 文档到文件
    doc_file = ""
    if doc_content:
        doc_file = f"{template_id}.md"
        doc_path = TEMPLATES_DIR / doc_file
        doc_path.write_text(doc_content, encoding="utf-8")

    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docker_templates
                (id, name, description, category, creator_id, doc_file, config_json, is_public,
                 deploy_type, raw_content, variables_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id, name, description, category, user.id,
                doc_file, "{}", 1 if is_public else 0,
                deploy_type, raw_content, json.dumps(variables), now, now,
            ),
        )
        # 记录创建者为 owner
        _record_template_creator(conn, template_id, user.id)

    return {
        "id": template_id,
        "name": name,
        "description": description,
        "category": category,
        "creatorId": user.id,
        "docFile": doc_file,
        "isPublic": is_public,
        "deployType": deploy_type,
        "rawContent": raw_content,
        "variables": variables,
        "createdAt": now,
        "updatedAt": now,
    }


def update_template(template_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    """更新容器模板。

    权限：管理员 / 模板 owner / 拥有 tpl_edit 权限的用户。
    """
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_manage_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有编辑此模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )

        name = (payload.get("name") or row["name"]).strip() or row["name"]
        description = payload.get("description", row["description"])
        category = payload.get("category", row["category"])
        is_public = bool(payload.get("isPublic", bool(row["is_public"]))) if "isPublic" in payload else bool(row["is_public"])
        deploy_type = payload.get("deployType", row["deploy_type"] if "deploy_type" in row.keys() else "run")
        if deploy_type not in ("run", "compose"):
            deploy_type = "run"
        raw_content = payload.get("rawContent", row["raw_content"] if "raw_content" in row.keys() else "")
        variables = payload.get("variables")
        if variables is None:
            variables = [_normalize_variable(v) for v in (json.loads(row["variables_json"]) if "variables_json" in row.keys() and row["variables_json"] else [])]
        else:
            # 规范化变量声明（matcher → filter 等）
            variables = [_normalize_variable(v) for v in variables]
            # 确保所有占位符都有声明
            detected = detect_placeholders(raw_content)
            declared_names = {v["name"] for v in variables}
            for d in detected:
                if d["name"] not in declared_names:
                    variables.append(d)

        doc_file = row["doc_file"]
        if "docContent" in payload:
            if not doc_file:
                doc_file = f"{template_id}.md"
            doc_path = TEMPLATES_DIR / doc_file
            doc_path.write_text(payload["docContent"], encoding="utf-8")

        now = _now()
        conn.execute(
            """
            UPDATE docker_templates SET name=?, description=?, category=?, doc_file=?, is_public=?,
                deploy_type=?, raw_content=?, variables_json=?, updated_at=?
            WHERE id=?
            """,
            (name, description, category, doc_file, 1 if is_public else 0,
             deploy_type, raw_content, json.dumps(variables), now, template_id),
        )
        # 重新查询更新后的行
        updated_row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        roles = _get_template_roles(conn, template_id)

    return _row_to_template_dict(updated_row, roles=roles)


def delete_template(template_id: str, user: User) -> None:
    """删除模板。

    权限：管理员 / 模板 owner / 拥有 tpl_edit 权限的用户。
    """
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_manage_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有删除此模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )

        # 删除 MD 文件
        if row["doc_file"]:
            doc_path = TEMPLATES_DIR / row["doc_file"]
            if doc_path.exists():
                doc_path.unlink()

        conn.execute("DELETE FROM docker_template_roles WHERE template_id = ?", (template_id,))
        conn.execute("DELETE FROM docker_templates WHERE id = ?", (template_id,))


def list_templates(user: User) -> list[dict[str, Any]]:
    """列出模板。

    可见性规则：
    - 管理员：可见所有模板
    - 普通用户：公开模板（需 tpl_use 权限） + 自己是 owner/viewer 的模板
    """
    init_docker_database()
    with get_connection() as conn:
        if user.role == "admin":
            rows = conn.execute("SELECT * FROM docker_templates ORDER BY category, name").fetchall()
        else:
            # 公开模板 + 用户有角色的模板
            rows = conn.execute(
                """
                SELECT DISTINCT t.* FROM docker_templates t
                LEFT JOIN docker_template_roles r ON t.id = r.template_id
                WHERE t.is_public = 1 OR r.user_id = ?
                ORDER BY t.category, t.name
                """,
                (user.id,),
            ).fetchall()

        # 批量查询角色信息
        result = []
        for row in rows:
            roles = _get_template_roles(conn, row["id"])
            tpl = _row_to_template_dict(row, roles=roles)
            # 标记当前用户是否可管理
            tpl["canManage"] = (
                user.role == "admin"
                or user.id in roles["ownerUserIds"]
                or _user_can_edit_templates(conn, user)
            )
            tpl["canUse"] = _user_can_access_template(conn, row["id"], user)
            result.append(tpl)
    return result


def get_template(template_id: str, user: User) -> dict[str, Any]:
    """获取模板详情，包含 MD 文档内容和变量声明。

    权限：admin / owner / viewer / 公开模板 + tpl_use。
    """
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_access_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有查看此模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        roles = _get_template_roles(conn, template_id)

    result = _row_to_template_dict(row, include_content=True, roles=roles)
    result["canManage"] = (
        user.role == "admin"
        or user.id in roles["ownerUserIds"]
    )
    # 不要在列表返回中暴露 rawContent 中的敏感信息——但 get_template 是详情接口，需要返回
    return result


def list_my_templates(user: User) -> list[dict[str, Any]]:
    """列出当前用户作为 owner 的模板（用于模板管理界面）。"""
    init_docker_database()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.* FROM docker_templates t
            INNER JOIN docker_template_roles r ON t.id = r.template_id
            WHERE r.user_id = ? AND r.role = 'owner'
            ORDER BY t.updated_at DESC
            """,
            (user.id,),
        ).fetchall()
        result = []
        for row in rows:
            roles = _get_template_roles(conn, row["id"])
            tpl = _row_to_template_dict(row, roles=roles)
            tpl["canManage"] = True
            result.append(tpl)
    return result


def list_all_templates_for_admin(user: User) -> list[dict[str, Any]]:
    """管理员查看所有模板（含角色信息）。"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以查看所有模板", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM docker_templates ORDER BY updated_at DESC").fetchall()
        result = []
        for row in rows:
            roles = _get_template_roles(conn, row["id"])
            tpl = _row_to_template_dict(row, roles=roles)
            tpl["canManage"] = True
            # 查询用户名
            creator_name = None
            if roles["creatorUserId"]:
                u = conn.execute("SELECT username, display_name FROM users WHERE id=?", (roles["creatorUserId"],)).fetchone()
                if u:
                    creator_name = u["display_name"] or u["username"]
            tpl["creatorName"] = creator_name
            result.append(tpl)
    return result


# ==============================================================
# 模板角色管理（参照容器资源角色模型）
# ==============================================================

def assign_template_roles(
    template_id: str,
    owner_user_ids: list[str],
    viewer_user_ids: list[str],
    user: User,
) -> dict[str, Any]:
    """设置模板的多角色绑定（管理员或模板 owner 可调用）。

    - owner_user_ids: 所有者列表（可多人，可编辑/删除模板并管理查看者）
    - viewer_user_ids: 查看者列表（可查看并使用非公开模板）
    - 创建者角色由系统自动记录，不可通过此接口修改
    """
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_manage_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有管理此模板角色的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )

        # 校验用户存在性
        all_user_ids = set(owner_user_ids) | set(viewer_user_ids)
        for uid in all_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        now = _now()
        # 清除 owner / viewer 记录（保留 creator 信息在 docker_templates.creator_id）
        conn.execute(
            "DELETE FROM docker_template_roles WHERE template_id=? AND role IN ('owner','viewer')",
            (template_id,),
        )
        for uid in owner_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_template_roles (id, template_id, user_id, role, assigned_at) VALUES (?,?,?,?,?)",
                    (_new_id(), template_id, uid, "owner", now),
                )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_template_roles (id, template_id, user_id, role, assigned_at) VALUES (?,?,?,?,?)",
                    (_new_id(), template_id, uid, "viewer", now),
                )

    roles = _get_template_roles(conn, template_id)
    return {
        "success": True,
        "templateId": template_id,
        "ownerUserIds": roles["ownerUserIds"],
        "viewerUserIds": roles["viewerUserIds"],
        "creatorUserId": roles["creatorUserId"],
        "updatedAt": now,
    }


def set_template_viewers(
    template_id: str,
    viewer_user_ids: list[str],
    user: User,
) -> dict[str, Any]:
    """模板 owner 修改查看者列表（仅 owner 或管理员可调用）。"""
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_manage_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您不是该模板的所有者，无法修改查看者列表",
                status_code=403,
                tool_id=TOOL_ID,
            )

        # 校验用户存在性
        for uid in viewer_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        # 不能把 owner 加入 viewer
        owner_ids = {r["user_id"] for r in conn.execute(
            "SELECT user_id FROM docker_template_roles WHERE template_id=? AND role='owner'",
            (template_id,),
        ).fetchall()}
        viewer_user_ids = [uid for uid in viewer_user_ids if uid not in owner_ids]

        now = _now()
        conn.execute(
            "DELETE FROM docker_template_roles WHERE template_id=? AND role='viewer'",
            (template_id,),
        )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_template_roles (id, template_id, user_id, role, assigned_at) VALUES (?,?,?,?,?)",
                    (_new_id(), template_id, uid, "viewer", now),
                )

    return {
        "success": True,
        "templateId": template_id,
        "viewerUserIds": viewer_user_ids,
        "updatedAt": now,
    }


def get_template_roles(template_id: str, user: User) -> dict[str, Any]:
    """获取模板角色信息（管理员或可查看该模板的用户）。"""
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_access_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有查看此模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        roles = _get_template_roles(conn, template_id)
        # 查询用户显示名
        def _user_info(uid: str) -> dict[str, Any]:
            u = conn.execute("SELECT id, username, display_name FROM users WHERE id=?", (uid,)).fetchone()
            if u:
                return {"userId": u["id"], "username": u["username"], "displayName": u["display_name"]}
            return {"userId": uid, "username": uid, "displayName": uid}

        return {
            "templateId": template_id,
            "creatorUserId": roles["creatorUserId"],
            "creator": _user_info(roles["creatorUserId"]) if roles["creatorUserId"] else None,
            "ownerUserIds": roles["ownerUserIds"],
            "owners": [_user_info(uid) for uid in roles["ownerUserIds"]],
            "viewerUserIds": roles["viewerUserIds"],
            "viewers": [_user_info(uid) for uid in roles["viewerUserIds"]],
            "canManage": _user_can_manage_template(conn, template_id, user),
        }


# ==============================================================
# 从模板创建容器
# ==============================================================

def _substitute_placeholders(raw_content: str, values: dict[str, str]) -> str:
    """将 raw_content 中的 {{VAR}} 占位符替换为实际值。

    未提供值的占位符使用变量声明中的 defaultValue，若 defaultValue 也为空则替换为空字符串。
    """
    def _replace(m: _re.Match) -> str:
        var_name = m.group(1)
        return str(values.get(var_name, ""))
    return _PLACEHOLDER_RE.sub(_replace, raw_content)


def create_from_template(
    server_id: str,
    template_id: str,
    overrides: dict[str, Any],
    user: User,
    gpus: str = "",
) -> dict[str, Any]:
    """从模板创建容器。

    1. 获取模板，校验访问权限
    2. 合并变量默认值和用户覆盖值
    3. 替换 raw_content 中的占位符
    4. 根据 deploy_type 调用 create_container_run_raw 或 create_container_compose
    """
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not _user_can_access_template(conn, template_id, user):
            raise ToolboxError(
                "PERMISSION_DENIED",
                "您没有使用此模板的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        # 校验用户在该服务器上有从模板创建容器的权限
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not perms.get("server_visible") or not perms.get("ctr_create_template"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有在此服务器从模板创建容器的权限",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

    template = _row_to_template_dict(row)
    variables = template["variables"]
    deploy_type = template["deployType"]
    raw_content = template["rawContent"]

    # 构建变量值：defaultValue → overrides
    values: dict[str, str] = {}
    for var in variables:
        var_name = var.get("name", "")
        default_val = var.get("defaultValue", "")
        values[var_name] = str(default_val) if default_val else ""
    # 用户覆盖（_projectName 等以下划线开头的为系统参数，不参与变量校验）
    for key, val in overrides.items():
        values[key] = str(val)

    # 校验变量值是否符合筛选条件（文本通配符匹配 / 数字范围检查 / 选项校验）
    var_by_name = {v.get("name", ""): v for v in variables}
    for var in variables:
        var_name = var.get("name", "")
        if not var_name:
            continue
        raw_val = values.get(var_name, "")
        # 仅校验由用户覆盖的变量值（含默认值），跳过系统参数
        ok, msg = _validate_variable_value(var, raw_val)
        if not ok:
            raise ToolboxError(
                "INVALID_VARIABLE",
                f"变量 {var_name}：{msg}",
                status_code=400,
                tool_id=TOOL_ID,
            )
    # 从 values 中剔除系统参数（不参与占位符替换）
    values = {k: v for k, v in values.items() if not k.startswith("_") and k in var_by_name}

    # 如果有 gpus 参数，需要注入到命令中
    # 对于 run 模式：在 docker run -d 后面注入 --gpus 参数
    # 对于 compose 模式：不自动注入（用户应在 YAML 中配置）
    final_content = _substitute_placeholders(raw_content, values)

    if gpus and deploy_type == "run":
        # 在 docker run -d 后面注入 --gpus 参数
        final_content = _inject_gpus_to_run_cmd(final_content, gpus)

    if deploy_type == "compose":
        project_name = overrides.get("_projectName", "") or template["name"].lower().replace(" ", "_")
        return create_container_compose(server_id, final_content, user, project_name)
    else:
        return create_container_run_raw(server_id, final_content, user)


def _inject_gpus_to_run_cmd(cmd: str, gpus: str) -> str:
    """在 docker run 命令中注入 --gpus 参数（如果尚不存在）。"""
    # 检查是否已有 --gpus 参数
    if "--gpus" in cmd:
        return cmd
    # 在 docker run -d 后面插入 --gpus
    # 匹配 docker run -d 或 docker run --detach
    match = _re.match(r'^(docker\s+run\s+(?:-d|--detach\b))\s*', cmd, _re.IGNORECASE)
    if match:
        return f"{match.group(1)} --gpus {gpus} {cmd[match.end():]}"
    # 回退：在 docker run 后面插入
    match = _re.match(r'^(docker\s+run\b)\s*', cmd, _re.IGNORECASE)
    if match:
        return f"{match.group(1)} --gpus {gpus} {cmd[match.end():]}"
    return cmd
