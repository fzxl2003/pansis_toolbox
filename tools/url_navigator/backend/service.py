"""URL navigator service: SQLite-based per-user storage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.db.database import user_tool_connection_context
from backend.app.services.data_management import DataCategory, register_tool_categories

TOOL_ID = "url_navigator"
DEFAULT_LINKS_PATH = Path(__file__).resolve().parents[1] / "default_links.json"

_initialized_dbs: set[str] = set()


def init_database(user_id: str) -> None:
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS url_navigator_links (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '未分类',
                strategy TEXT NOT NULL DEFAULT 'priority_first',
                entries_json TEXT NOT NULL DEFAULT '[]',
                icon_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_url_nav_created ON url_navigator_links(created_at)"
        )
    _initialized_dbs.add(user_id)


register_tool_categories(TOOL_ID, [
    DataCategory(
        name="links",
        tables=["url_navigator_links"],
        time_column="created_at",
        description="导航链接数据",
        storage="user_tool_db",
    ),
])


def list_links(user_id: str) -> list[dict[str, Any]]:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        rows = conn.execute(
            "SELECT * FROM url_navigator_links ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_link(r) for r in rows]


def get_link(user_id: str, link_id: str) -> dict[str, Any] | None:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)
        ).fetchone()
    return _row_to_link(row) if row else None


def create_link(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_database(user_id)
    link_id = uuid4().hex
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            INSERT INTO url_navigator_links
                (id, name, description, category, strategy, entries_json, icon_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                data["name"],
                data.get("description", ""),
                data.get("category", "未分类"),
                data.get("strategy", "priority_first"),
                json.dumps(data.get("entries", []), ensure_ascii=False),
                json.dumps(data.get("icon", {}), ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)).fetchone()
    return _row_to_link(row)


def update_link(user_id: str, link_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    init_database(user_id)
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute("SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)).fetchone()
        if existing is None:
            return None
        merged = _row_to_link(existing)
        merged.update(data)
        conn.execute(
            """
            UPDATE url_navigator_links SET
                name = ?, description = ?, category = ?, strategy = ?,
                entries_json = ?, icon_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged["name"],
                merged.get("description", ""),
                merged.get("category", "未分类"),
                merged.get("strategy", "priority_first"),
                json.dumps(merged.get("entries", []), ensure_ascii=False),
                json.dumps(merged.get("icon", {}), ensure_ascii=False),
                now,
                link_id,
            ),
        )
        row = conn.execute("SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)).fetchone()
    return _row_to_link(row) if row else None


def update_link_icon(user_id: str, link_id: str, icon: dict[str, Any]) -> dict[str, Any] | None:
    init_database(user_id)
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute("SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)).fetchone()
        if existing is None:
            return None
        conn.execute(
            "UPDATE url_navigator_links SET icon_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(icon, ensure_ascii=False), now, link_id),
        )
        row = conn.execute("SELECT * FROM url_navigator_links WHERE id = ?", (link_id,)).fetchone()
    return _row_to_link(row) if row else None


def delete_link(user_id: str, link_id: str) -> bool:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        cursor = conn.execute("DELETE FROM url_navigator_links WHERE id = ?", (link_id,))
        return cursor.rowcount > 0


def reset_links(user_id: str) -> list[dict[str, Any]]:
    """Replace all links with defaults."""
    init_database(user_id)
    defaults = _default_links()
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute("DELETE FROM url_navigator_links")
        for raw in defaults:
            link_id = raw.get("id") or uuid4().hex
            conn.execute(
                """
                INSERT INTO url_navigator_links
                    (id, name, description, category, strategy, entries_json, icon_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    raw["name"],
                    raw.get("description", ""),
                    raw.get("category", "未分类"),
                    raw.get("strategy", "priority_first"),
                    json.dumps(raw.get("entries", []), ensure_ascii=False),
                    json.dumps(raw.get("icon", {}), ensure_ascii=False),
                    raw.get("createdAt", now),
                    raw.get("updatedAt", now),
                ),
            )
        rows = conn.execute("SELECT * FROM url_navigator_links ORDER BY created_at DESC").fetchall()
    return [_row_to_link(r) for r in rows]


def _row_to_link(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "strategy": row["strategy"],
        "entries": json.loads(row["entries_json"]),
        "icon": json.loads(row["icon_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _default_links() -> list[dict[str, Any]]:
    if not DEFAULT_LINKS_PATH.exists():
        return []
    return json.loads(DEFAULT_LINKS_PATH.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
