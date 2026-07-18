from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.requests import HTTPConnection

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database, list_user_tool_dbs
from backend.app.registry.models import RegisteredTool
from backend.app.services.auth_service import User, list_users


# Tools that still store tables in the shared platform database.
# Other tools (experiment_monitor, server_monitor, ssh_workspace,
# memo_demo, url_navigator, web_proxy) now use per-user SQLite databases
# and are cleaned up by deleting the per-user ``data.db`` files.
TOOL_TABLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "docker_manager": ("docker_",),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tool_access_tables() -> None:
    init_database()


def can_access_tool(tool_id: str, user: User | None) -> bool:
    ensure_tool_access_tables()
    if user and user.is_super_admin:
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
    """Clear ALL data for a tool: platform DB tables + per-user DB files + file storage.

    This is the nuclear option used by the admin "清除全部" button.  For
    surgical deletion by category/time, use ``data_management.delete_data``.
    """
    ensure_tool_access_tables()
    dropped_tables = _drop_tool_tables(tool.tool_id)
    removed_paths = _remove_tool_paths(tool.tool_id)
    # Also remove per-user DB files for tools that use per-user databases.
    removed_db_files = _remove_per_user_dbs(tool.tool_id)
    return {
        "toolId": tool.tool_id,
        "droppedTables": dropped_tables,
        "removedPaths": removed_paths + removed_db_files,
    }


def clear_user_tool_storage(tool_id: str, user_id: str) -> dict[str, Any]:
    """Clear file storage for a specific user+tool combination.

    Removes the user's per-tool directory which includes the per-user
    ``data.db`` file and any auxiliary file storage (icons, etc.).
    """
    ensure_tool_access_tables()
    removed_paths = _remove_user_tool_paths(tool_id, user_id)
    return {"toolId": tool_id, "userId": user_id, "droppedTables": [], "removedPaths": removed_paths}


def clear_user_storage(user_id: str) -> dict[str, Any]:
    """Clear all tool file storage for a specific user (user-scoped files only)."""
    ensure_tool_access_tables()
    settings = get_settings()
    user_root = settings.storage_dir / "user_data" / user_id / "tools"
    removed_paths: list[str] = []
    if user_root.exists() and _is_within_storage(user_root.resolve(), settings.storage_dir.resolve()):
        for child in sorted(user_root.iterdir()):
            resolved = child.resolve()
            if not _is_within_storage(resolved, settings.storage_dir.resolve()):
                continue
            label = str(resolved)
            if resolved.is_dir():
                shutil.rmtree(resolved)
                removed_paths.append(label)
            elif resolved.exists():
                resolved.unlink()
                removed_paths.append(label)
    return {"userId": user_id, "droppedTables": [], "removedPaths": removed_paths}


def get_storage_usage(tools: list[RegisteredTool]) -> dict[str, Any]:
    """Return detailed storage usage breakdown per tool and per user.

    Structure::

        {
          "grandTotal": int,
          "tools": [
            {"toolId", "toolName", "totalBytes", "sharedBytes", "userBytes", "dbBytes"}
          ],
          "users": [
            {"userId", "username", "displayName", "totalBytes"}
          ],
          "matrix": [
            {"userId", "toolId", "bytes"}
          ]
        }
    """
    ensure_tool_access_tables()
    settings = get_settings()
    user_data_root = settings.storage_dir / "user_data"

    all_users = list_users()
    user_info = {u.id: u for u in all_users}

    # Collect user-data directories that exist on disk (may include users since deleted).
    user_dirs: dict[str, Path] = {}
    if user_data_root.exists():
        for child in user_data_root.iterdir():
            if child.is_dir():
                user_dirs[child.name] = child

    # Ensure all known users are represented even if their dir is missing.
    for uid in user_info:
        user_dirs.setdefault(uid, user_data_root / uid)

    tool_ids = [tool.tool_id for tool in tools]

    # Per-tool shared file bytes (data/tools/<id> + temp/<id>).
    tool_shared_file_bytes: dict[str, int] = {}
    for tid in tool_ids:
        shared = _dir_size(settings.storage_dir / "data" / "tools" / tid)
        temp = _dir_size(settings.storage_dir / "temp" / tid)
        tool_shared_file_bytes[tid] = shared + temp

    # Per-tool DB bytes (via dbstat with graceful fallback).
    tool_db_bytes = _compute_tool_db_bytes(tool_ids)

    # Per-user-per-tool file bytes.
    matrix: dict[tuple[str, str], int] = {}
    for uid, uroot in user_dirs.items():
        tools_dir = uroot / "tools"
        if not tools_dir.exists():
            continue
        for tid in tool_ids:
            size = _dir_size(tools_dir / tid)
            if size > 0:
                matrix[(uid, tid)] = size

    # Aggregate per-tool totals.
    tool_rows: list[dict[str, Any]] = []
    for tool in tools:
        tid = tool.tool_id
        user_bytes = sum(v for (uid, t), v in matrix.items() if t == tid)
        shared_bytes = tool_shared_file_bytes.get(tid, 0)
        db_bytes = tool_db_bytes.get(tid, 0)
        tool_rows.append({
            "toolId": tid,
            "toolName": tool.manifest.name,
            "totalBytes": user_bytes + shared_bytes + db_bytes,
            "sharedBytes": shared_bytes + db_bytes,
            "userBytes": user_bytes,
            "dbBytes": db_bytes,
        })

    # Aggregate per-user totals.
    user_rows: list[dict[str, Any]] = []
    for uid, uroot in user_dirs.items():
        total = sum(v for (u, _t), v in matrix.items() if u == uid)
        info = user_info.get(uid)
        if info is not None:
            username = info.username
            display_name = info.display_name
        else:
            username = uid
            display_name = uid
        user_rows.append({
            "userId": uid,
            "username": username,
            "displayName": display_name,
            "totalBytes": total,
        })

    # Sort: tools by total desc, users by total desc.
    tool_rows.sort(key=lambda r: r["totalBytes"], reverse=True)
    user_rows.sort(key=lambda r: r["totalBytes"], reverse=True)

    grand_total = sum(r["totalBytes"] for r in tool_rows)

    matrix_rows = [
        {"userId": uid, "toolId": tid, "bytes": size}
        for (uid, tid), size in sorted(matrix.items())
    ]

    return {
        "grandTotal": grand_total,
        "tools": tool_rows,
        "users": user_rows,
        "matrix": matrix_rows,
    }


def get_user_storage_usage(user: User, tools: list[RegisteredTool]) -> dict[str, Any]:
    """Return storage usage for a single user (self-service view)."""
    ensure_tool_access_tables()
    settings = get_settings()
    tools_dir = settings.storage_dir / "user_data" / user.id / "tools"

    tool_rows: list[dict[str, Any]] = []
    total = 0
    for tool in tools:
        size = _dir_size(tools_dir / tool.tool_id)
        tool_rows.append({
            "toolId": tool.tool_id,
            "toolName": tool.manifest.name,
            "bytes": size,
        })
        total += size
    tool_rows.sort(key=lambda r: r["bytes"], reverse=True)
    return {"userId": user.id, "totalBytes": total, "tools": tool_rows}


def enforce_tool_access_dependency(tool_id: str):
    def dependency(connection: HTTPConnection) -> None:
        from backend.app.core.security import get_optional_user

        require_tool_access(tool_id, get_optional_user(connection))

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


def _remove_user_tool_paths(tool_id: str, user_id: str) -> list[str]:
    """Remove user-scoped file storage for a single user+tool combination."""
    settings = get_settings()
    safe_tool_id = tool_id.replace("/", "_").replace("\\", "_")
    path = settings.storage_dir / "user_data" / user_id / "tools" / safe_tool_id
    removed: list[str] = []
    resolved = path.resolve()
    if not _is_within_storage(resolved, settings.storage_dir.resolve()):
        return removed
    if resolved.is_dir():
        shutil.rmtree(resolved)
        removed.append(str(resolved))
    elif resolved.exists():
        resolved.unlink()
        removed.append(str(resolved))
    return removed


def _remove_per_user_dbs(tool_id: str) -> list[str]:
    """Delete per-user ``data.db`` files for *tool_id* across all users.

    This is a safety net: ``_remove_tool_paths`` already deletes the entire
    ``user_data/<uid>/tools/<tool_id>`` directory, but we call this explicitly
    to report the DB files separately and to handle any orphaned DB files
    that might exist outside the standard directory structure.
    """
    removed: list[str] = []
    for uid, db_path in list_user_tool_dbs(tool_id):
        try:
            if db_path.exists():
                db_path.unlink()
                removed.append(str(db_path))
        except OSError:
            pass
    return removed


def _dir_size(path: Path) -> int:
    """Recursively compute total file size under *path* (0 if missing)."""
    if not path.exists():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _compute_tool_db_bytes(tool_ids: list[str]) -> dict[str, int]:
    """Estimate per-tool database table sizes using the ``dbstat`` virtual table.

    Falls back to 0 for every tool if ``dbstat`` is unavailable.
    """
    result: dict[str, int] = {tid: 0 for tid in tool_ids}
    try:
        with get_connection() as connection:
            # Verify dbstat is available.
            try:
                connection.execute("SELECT COUNT(*) FROM dbstat LIMIT 1").fetchone()
            except Exception:
                return result
            rows = connection.execute(
                "SELECT name, SUM(pgsize) AS size FROM dbstat GROUP BY name"
            ).fetchall()
    except Exception:
        return result

    for tid in tool_ids:
        prefixes = TOOL_TABLE_PREFIXES.get(tid, (f"{tid}_",))
        total = 0
        for row in rows:
            name = row["name"]
            if name.startswith("platform_"):
                continue
            if any(name.startswith(prefix) for prefix in prefixes):
                size = row["size"]
                if size is not None:
                    total += int(size)
        result[tid] = total
    return result


def _is_within_storage(path: Path, storage_root: Path) -> bool:
    try:
        path.relative_to(storage_root)
        return True
    except ValueError:
        return False
