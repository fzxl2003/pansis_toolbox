"""Web proxy service: SQLite-based per-user session storage.

The web_proxy tool maintains a pointer to the active rammerhead sidecar
session for each user.  Previously this was stored in a ``session.json``
file; it now lives in the per-user per-tool SQLite database so that it
participates in the unified data-management framework (category registry,
time-based pruning, etc.).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.db.database import user_tool_connection_context
from backend.app.services.data_management import DataCategory, register_tool_categories

TOOL_ID = "web_proxy"

_initialized_dbs: set[str] = set()


def init_database(user_id: str) -> None:
    """Create the web_proxy session table if it does not yet exist."""
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_proxy_sessions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_proxy_sessions_created ON web_proxy_sessions(created_at)"
        )
    _initialized_dbs.add(user_id)


# ============================================================
# Data category registration
# ============================================================

register_tool_categories(TOOL_ID, [
    DataCategory(
        name="sessions",
        tables=["web_proxy_sessions"],
        time_column="created_at",
        description="网页代理会话记录",
        storage="user_tool_db",
    ),
])


# ============================================================
# Session CRUD
# ============================================================

def get_session(user_id: str) -> dict[str, Any] | None:
    """Return the user's most recent proxy session, or ``None``."""
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM web_proxy_sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return _row_to_session(row) if row else None


def save_session(user_id: str, session_id: str) -> dict[str, Any]:
    """Persist (or update) the user's proxy session pointer."""
    init_database(user_id)
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute(
            "SELECT id FROM web_proxy_sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE web_proxy_sessions SET session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, now, existing["id"]),
            )
            row = conn.execute(
                "SELECT * FROM web_proxy_sessions WHERE id = ?", (existing["id"],)
            ).fetchone()
        else:
            import uuid
            row_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO web_proxy_sessions (id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (row_id, session_id, now, now),
            )
            row = conn.execute(
                "SELECT * FROM web_proxy_sessions WHERE id = ?", (row_id,)
            ).fetchone()
    return _row_to_session(row)


def clear_session(user_id: str) -> bool:
    """Delete all session rows for the user. Returns ``True`` if any row was removed."""
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        cursor = conn.execute("DELETE FROM web_proxy_sessions")
        return cursor.rowcount > 0


def _row_to_session(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
