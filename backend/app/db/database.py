from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.app.core.config import get_settings


def get_connection() -> sqlite3.Connection:
    db_path = get_settings().platform_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def connection_context() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


# ============================================================
# Per-user per-tool database connections
# ============================================================

def get_user_tool_db_path(user_id: str, tool_id: str) -> Path:
    """Return the path to a user's per-tool SQLite database.

    The database file lives at ``storage/user_data/<user_id>/tools/<tool_id>/data.db``
    so that all user-specific data stays within the user's folder.
    """
    safe_tool_id = tool_id.replace("/", "_").replace("\\", "_")
    path = get_settings().storage_dir / "user_data" / user_id / "tools" / safe_tool_id / "data.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_user_tool_connection(user_id: str, tool_id: str) -> sqlite3.Connection:
    """Open a connection to a user's per-tool database."""
    db_path = get_user_tool_db_path(user_id, tool_id)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def user_tool_connection_context(user_id: str, tool_id: str) -> Iterator[sqlite3.Connection]:
    """Context manager for a per-user per-tool database connection.

    Commits on success, always closes on exit.
    """
    connection = get_user_tool_connection(user_id, tool_id)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def list_user_tool_dbs(tool_id: str) -> list[tuple[str, Path]]:
    """List all existing per-user databases for *tool_id*.

    Returns a list of ``(user_id, db_path)`` tuples.  Used by background
    schedulers that need to iterate over every user's database.
    """
    settings = get_settings()
    user_data_root = settings.storage_dir / "user_data"
    result: list[tuple[str, Path]] = []
    if not user_data_root.exists():
        return result
    for user_dir in user_data_root.iterdir():
        if not user_dir.is_dir():
            continue
        db_path = user_dir / "tools" / tool_id / "data.db"
        if db_path.exists():
            result.append((user_dir.name, db_path))
    return result


def init_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS platform_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_email_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_tool_visibility (
                tool_id TEXT PRIMARY KEY,
                global_public INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_tool_user_access (
                tool_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                PRIMARY KEY(tool_id, user_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        _ensure_column(connection, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
        _ensure_column(connection, "users", "disabled", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
