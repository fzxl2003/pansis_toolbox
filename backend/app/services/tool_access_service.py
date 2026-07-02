from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database
from backend.app.registry.models import RegisteredTool
from backend.app.services.auth_service import User, list_users


TOOL_TABLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "docker_manager": ("docker_",),
    "experiment_monitor": ("em_",),
    "server_monitor": ("monitor_",),
    "ssh_workspace": ("ssh_",),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tool_access_tables() -> None:
    init_database()


def can_access_tool(tool_id: str, user: User | None) -> bool:
    ensure_tool_access_tables()
    if user and user.role == "admin":
        return True
    with get_connection() as connection:
        policy = connection.execute(
            "SELECT global_public FROM platform_tool_visibility WHERE tool_id = ?",
            (tool_id,),
        ).fetchone()
        if policy is None or policy["global_public"]:
            return True
        if user is None:
            return False
        grant = connection.execute(
            "SELECT 1 FROM platform_tool_user_access WHERE tool_id = ? AND user_id = ?",
            (tool_id, user.id),
        ).fetchone()
        return grant is not None


def require_tool_access(tool_id: str, user: User | None) -> None:
    if can_access_tool(tool_id, user):
        return
    if user is None:
        raise ToolboxError(
            "LOGIN_REQUIRED",
            "请先登录后再使用该工具",
            status_code=401,
            extra={"loginUrl": "/login"},
            tool_id=tool_id,
        )
    raise ToolboxError("TOOL_ACCESS_DENIED", "当前账号无权使用该工具", status_code=403, tool_id=tool_id)


def visible_tools(tools: list[RegisteredTool], user: User | None) -> list[RegisteredTool]:
    return [tool for tool in tools if can_access_tool(tool.tool_id, user)]


def list_tool_access(tools: list[RegisteredTool]) -> list[dict[str, Any]]:
    ensure_tool_access_tables()
    users = [user for user in list_users() if not user.disabled]
    user_map = {user.id: user for user in users}
    with get_connection() as connection:
        policies = {
            row["tool_id"]: bool(row["global_public"])
            for row in connection.execute("SELECT tool_id, global_public FROM platform_tool_visibility").fetchall()
        }
        grants_by_tool: dict[str, list[str]] = {}
        for row in connection.execute("SELECT tool_id, user_id FROM platform_tool_user_access ORDER BY granted_at ASC").fetchall():
            if row["user_id"] in user_map:
                grants_by_tool.setdefault(row["tool_id"], []).append(row["user_id"])

    return [
        {
            "tool": tool.public_dict(),
            "globalPublic": policies.get(tool.tool_id, True),
            "allowedUsers": [
                {
                    "id": user_id,
                    "username": user_map[user_id].username,
                    "displayName": user_map[user_id].display_name,
                }
                for user_id in grants_by_tool.get(tool.tool_id, [])
            ],
        }
        for tool in tools
    ]


def update_tool_access(tool_id: str, global_public: bool, allowed_user_ids: list[str]) -> dict[str, Any]:
    ensure_tool_access_tables()
    clean_user_ids = sorted(set(user_id for user_id in allowed_user_ids if user_id))
    valid_users = {user.id for user in list_users() if not user.disabled}
    invalid = [user_id for user_id in clean_user_ids if user_id not in valid_users]
    if invalid:
        raise ToolboxError("INVALID_TOOL_ACCESS_USER", "授权用户不存在或已禁用", status_code=400, tool_id=tool_id)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO platform_tool_visibility (tool_id, global_public, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(tool_id) DO UPDATE SET
                global_public = excluded.global_public,
                updated_at = excluded.updated_at
            """,
            (tool_id, 1 if global_public else 0, now_iso()),
        )
        connection.execute("DELETE FROM platform_tool_user_access WHERE tool_id = ?", (tool_id,))
        connection.executemany(
            """
            INSERT INTO platform_tool_user_access (tool_id, user_id, granted_at)
            VALUES (?, ?, ?)
            """,
            [(tool_id, user_id, now_iso()) for user_id in clean_user_ids],
        )
        connection.commit()

    users_by_id = {user.id: user for user in list_users()}
    return {
        "toolId": tool_id,
        "globalPublic": global_public,
        "allowedUsers": [
            {
                "id": user_id,
                "username": users_by_id[user_id].username,
                "displayName": users_by_id[user_id].display_name,
            }
            for user_id in clean_user_ids
            if user_id in users_by_id
        ],
    }


def clear_tool_storage(tool: RegisteredTool) -> dict[str, Any]:
    ensure_tool_access_tables()
    dropped_tables = _drop_tool_tables(tool.tool_id)
    removed_paths = _remove_tool_paths(tool.tool_id)
    return {"toolId": tool.tool_id, "droppedTables": dropped_tables, "removedPaths": removed_paths}


def enforce_tool_access_dependency(tool_id: str):
    def dependency(request: Request) -> None:
        from backend.app.core.security import get_optional_user

        require_tool_access(tool_id, get_optional_user(request))

    return dependency


def _drop_tool_tables(tool_id: str) -> list[str]:
    prefixes = TOOL_TABLE_PREFIXES.get(tool_id, (f"{tool_id}_",))
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        table_names = [
            row["name"]
            for row in rows
            if any(row["name"].startswith(prefix) for prefix in prefixes)
            and not row["name"].startswith("platform_")
        ]
        for table_name in table_names:
            connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        connection.commit()
    return table_names


def _remove_tool_paths(tool_id: str) -> list[str]:
    settings = get_settings()
    candidates = [
        settings.storage_dir / "data" / "tools" / tool_id,
        settings.storage_dir / "temp" / tool_id,
        settings.storage_dir / tool_id,
    ]
    user_data_root = settings.storage_dir / "user_data"
    if user_data_root.exists():
        candidates.extend(user_root / "tools" / tool_id for user_root in user_data_root.iterdir() if user_root.is_dir())

    removed: list[str] = []
    for path in candidates:
        resolved = path.resolve()
        if not _is_within_storage(resolved, settings.storage_dir.resolve()):
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved)
            removed.append(str(resolved))
        elif resolved.exists():
            resolved.unlink()
            removed.append(str(resolved))
    return removed


def _is_within_storage(path: Path, storage_root: Path) -> bool:
    try:
        path.relative_to(storage_root)
        return True
    except ValueError:
        return False
