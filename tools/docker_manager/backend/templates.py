from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})
from .containers import create_container_run

# ==============================================================
# 模板管理
# ==============================================================

def create_template(payload: dict[str, Any], user: User) -> dict[str, Any]:
    """创建容器模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以创建模板", status_code=403, tool_id=TOOL_ID)

    template_id = _new_id()
    name = payload.get("name", "").strip()
    if not name:
        raise ToolboxError("INVALID_NAME", "模板名称不能为空", status_code=400, tool_id=TOOL_ID)

    description = payload.get("description", "")
    category = payload.get("category", "general")
    doc_content = payload.get("docContent", "")
    config = payload.get("config", {})
    is_public = bool(payload.get("isPublic", True))

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
            INSERT INTO docker_templates (id, name, description, category, creator_id, doc_file, config_json, is_public, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id, name, description, category, user.id,
                doc_file, json.dumps(config), 1 if is_public else 0, now, now,
            ),
        )

    return {
        "id": template_id,
        "name": name,
        "description": description,
        "category": category,
        "docFile": doc_file,
        "config": config,
        "isPublic": is_public,
        "createdAt": now,
        "updatedAt": now,
    }


def update_template(template_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    """更新容器模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以修改模板", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

        name = payload.get("name", row["name"]).strip() or row["name"]
        description = payload.get("description", row["description"])
        category = payload.get("category", row["category"])
        config = payload.get("config", json.loads(row["config_json"]))
        is_public = bool(payload.get("isPublic", bool(row["is_public"])))

        doc_file = row["doc_file"]
        if "docContent" in payload:
            if not doc_file:
                doc_file = f"{template_id}.md"
            doc_path = TEMPLATES_DIR / doc_file
            doc_path.write_text(payload["docContent"], encoding="utf-8")

        now = _now()
        conn.execute(
            """
            UPDATE docker_templates SET name=?, description=?, category=?, doc_file=?, config_json=?, is_public=?, updated_at=?
            WHERE id=?
            """,
            (name, description, category, doc_file, json.dumps(config), 1 if is_public else 0, now, template_id),
        )

    return {"id": template_id, "name": name, "description": description, "category": category,
            "docFile": doc_file, "config": config, "isPublic": is_public, "updatedAt": now}


def delete_template(template_id: str, user: User) -> None:
    """删除模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以删除模板", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

        # 删除 MD 文件
        if row["doc_file"]:
            doc_path = TEMPLATES_DIR / row["doc_file"]
            if doc_path.exists():
                doc_path.unlink()

        conn.execute("DELETE FROM docker_templates WHERE id = ?", (template_id,))


def list_templates(user: User) -> list[dict[str, Any]]:
    """列出模板"""
    init_docker_database()
    with get_connection() as conn:
        if user.role == "admin":
            rows = conn.execute("SELECT * FROM docker_templates ORDER BY category, name").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM docker_templates WHERE is_public = 1 ORDER BY category, name"
            ).fetchall()

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "creatorId": row["creator_id"],
            "hasDoc": bool(row["doc_file"]),
            "config": json.loads(row["config_json"]),
            "isPublic": bool(row["is_public"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def get_template(template_id: str, user: User) -> dict[str, Any]:
    """获取模板详情，包含 MD 文档内容"""
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not row["is_public"] and user.role != "admin":
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

    doc_content = ""
    if row["doc_file"]:
        doc_path = TEMPLATES_DIR / row["doc_file"]
        if doc_path.exists():
            doc_content = doc_path.read_text(encoding="utf-8")

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "creatorId": row["creator_id"],
        "docContent": doc_content,
        "config": json.loads(row["config_json"]),
        "isPublic": bool(row["is_public"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_from_template(
    server_id: str,
    template_id: str,
    overrides: dict[str, Any],
    user: User,
    gpus: str = "",
) -> dict[str, Any]:
    """从模板创建容器。gpus 为 GPU 挂载参数，优先级高于模板自身配置（若非空）"""
    init_docker_database()
    template = get_template(template_id, user)
    config = template["config"].copy()

    # 合并用户覆盖参数
    for key, val in overrides.items():
        config[key] = val

    # 若调用方传入了 gpus 参数，则覆盖模板配置中的 gpus
    if gpus:
        config["gpus"] = gpus

    deploy_type = config.get("type", "run")
    if deploy_type == "compose":
        yaml_content = config.get("composeYaml", "")
        project_name = config.get("projectName", template["name"].lower().replace(" ", "_"))
        return create_container_compose(server_id, yaml_content, user, project_name)
    else:
        return create_container_run(server_id, config, user)


