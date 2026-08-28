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
    """Create the web proxy's user-isolated tables and apply local migrations."""
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_proxy_sessions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                exit_server_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Existing installations created this table before SSH exits existed.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(web_proxy_sessions)")}
        if "exit_server_id" not in columns:
            conn.execute("ALTER TABLE web_proxy_sessions ADD COLUMN exit_server_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_proxy_sessions_created ON web_proxy_sessions(created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_proxy_test_sites (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_proxy_test_sites_created ON web_proxy_test_sites(created_at)"
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
    DataCategory(
        name="test_sites",
        tables=["web_proxy_test_sites"],
        time_column="created_at",
        description="网页代理出口测试站点",
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


def save_session(user_id: str, session_id: str, exit_server_id: str | None = None) -> dict[str, Any]:
    """Persist (or update) the user's proxy session pointer."""
    init_database(user_id)
    now = _now()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute(
            "SELECT id FROM web_proxy_sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE web_proxy_sessions SET session_id = ?, exit_server_id = ?, updated_at = ? WHERE id = ?",
                (session_id, exit_server_id, now, existing["id"]),
            )
            row = conn.execute(
                "SELECT * FROM web_proxy_sessions WHERE id = ?", (existing["id"],)
            ).fetchone()
        else:
            import uuid
            row_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO web_proxy_sessions (id, session_id, exit_server_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (row_id, session_id, exit_server_id, now, now),
            )
            row = conn.execute(
                "SELECT * FROM web_proxy_sessions WHERE id = ?", (row_id,)
            ).fetchone()
    return _row_to_session(row)


def set_session_exit(user_id: str, exit_server_id: str | None) -> dict[str, Any] | None:
    """Persist the selected exit for the user's active web proxy session."""
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute(
            "SELECT id FROM web_proxy_sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not existing:
            return None
        now = _now()
        conn.execute(
            "UPDATE web_proxy_sessions SET exit_server_id = ?, updated_at = ? WHERE id = ?",
            (exit_server_id, now, existing["id"]),
        )
        row = conn.execute("SELECT * FROM web_proxy_sessions WHERE id = ?", (existing["id"],)).fetchone()
    return _row_to_session(row)


def clear_session(user_id: str) -> bool:
    """Delete all session rows for the user. Returns ``True`` if any row was removed."""
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        cursor = conn.execute("DELETE FROM web_proxy_sessions")
        return cursor.rowcount > 0


# ============================================================
# Exit-test site CRUD
# ============================================================

def list_test_sites(user_id: str) -> list[dict[str, Any]]:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        rows = conn.execute(
            "SELECT * FROM web_proxy_test_sites ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_test_site(row) for row in rows]


def add_test_site(user_id: str, url: str) -> dict[str, Any]:
    """Store a user's test site, returning the existing row for duplicates."""
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        existing = conn.execute(
            "SELECT * FROM web_proxy_test_sites WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            return _row_to_test_site(existing)
        import uuid
        row_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO web_proxy_test_sites (id, url, created_at) VALUES (?, ?, ?)",
            (row_id, url, _now()),
        )
        row = conn.execute("SELECT * FROM web_proxy_test_sites WHERE id = ?", (row_id,)).fetchone()
    return _row_to_test_site(row)


def delete_test_site(user_id: str, site_id: str) -> bool:
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        cursor = conn.execute("DELETE FROM web_proxy_test_sites WHERE id = ?", (site_id,))
    return cursor.rowcount > 0


def _row_to_session(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "exitServerId": row["exit_server_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_test_site(row) -> dict[str, Any]:
    return {"id": row["id"], "url": row["url"], "createdAt": row["created_at"]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
