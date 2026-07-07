"""Memo demo service: SQLite-based per-user storage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.db.database import user_tool_connection_context
from backend.app.services.data_management import DataCategory, register_tool_categories

TOOL_ID = "memo_demo"

_initialized_dbs: set[str] = set()


def init_database(user_id: str) -> None:
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memo_demo_memos (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memo_demo_created ON memo_demo_memos(created_at)"
        )
    _initialized_dbs.add(user_id)


register_tool_categories(TOOL_ID, [
    DataCategory(
        name="memos",
        tables=["memo_demo_memos"],
        time_column="created_at",
        description="备忘录数据",
        storage="user_tool_db",
    ),
])


def list_memos(user_id: str) -> list[dict[str, Any]]:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        rows = conn.execute(
            "SELECT id, title, filename, size_bytes, created_at, updated_at FROM memo_demo_memos ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_summary(r) for r in rows]


def get_memo(user_id: str, memo_id: str) -> dict[str, Any]:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM memo_demo_memos WHERE id = ?", (memo_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_detail(row)


def create_memo(user_id: str, title: str, content: str) -> dict[str, Any]:
    init_database(user_id)
    memo_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            INSERT INTO memo_demo_memos (id, title, filename, content, size_bytes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (memo_id, title, f"{memo_id}.txt", content, len(content.encode("utf-8")), now, now),
        )
        row = conn.execute("SELECT * FROM memo_demo_memos WHERE id = ?", (memo_id,)).fetchone()
    return _row_to_detail(row)


def delete_memo(user_id: str, memo_id: str) -> bool:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        cursor = conn.execute("DELETE FROM memo_demo_memos WHERE id = ?", (memo_id,))
        return cursor.rowcount > 0


def _row_to_summary(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "sizeBytes": row["size_bytes"],
    }


def _row_to_detail(row) -> dict[str, Any]:
    summary = _row_to_summary(row)
    summary["content"] = row["content"]
    return summary
