from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import secrets
import shlex
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection
from backend.app.services.auth_service import User, get_user_by_session_token
from backend.app.services import ssh_connection_service
from backend.app.services.ssh_connection_service import SSHConnectionSpec

TOOL_ID = "ssh_workspace"
SCHEDULER_INTERVAL_SECONDS = 30
_SESSION_RE = re.compile(r"^\s*(?P<pid>\d+)\.(?P<name>[^\t\s]+)\s+\((?P<state>[^)]+)\)")
_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9_.-]+")

# ---- Database ----


def init_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ssh_servers (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                ssh_username TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'password',
                ssh_password_encrypted TEXT NOT NULL DEFAULT '',
                private_key_encrypted TEXT NOT NULL DEFAULT '',
                private_key_passphrase_encrypted TEXT NOT NULL DEFAULT '',
                has_screen INTEGER NOT NULL DEFAULT 0,
                last_test_status TEXT NOT NULL DEFAULT 'unknown',
                last_test_error TEXT NOT NULL DEFAULT '',
                last_tested_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_servers_owner
                ON ssh_servers(owner_user_id, enabled);

            CREATE TABLE IF NOT EXISTS ssh_command_history (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT,
                source TEXT NOT NULL DEFAULT 'terminal',
                command TEXT NOT NULL,
                exit_status INTEGER,
                screen_session TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_history_owner_time
                ON ssh_command_history(owner_user_id, created_at);

            CREATE TABLE IF NOT EXISTS ssh_command_templates (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                name TEXT NOT NULL,
                command TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                variables_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );

            CREATE TABLE IF NOT EXISTS ssh_screen_sessions (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                session_name TEXT NOT NULL,
                pid TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                created_by_tool INTEGER NOT NULL DEFAULT 1,
                command TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                checked_at TEXT,
                UNIQUE(owner_user_id, server_id, session_name),
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_screen_owner_server
                ON ssh_screen_sessions(owner_user_id, server_id);

            CREATE TABLE IF NOT EXISTS ssh_scheduled_tasks (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                name TEXT NOT NULL,
                command TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,
                screen_name_prefix TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_tasks_due
                ON ssh_scheduled_tasks(enabled, next_run_at);

            CREATE TABLE IF NOT EXISTS ssh_task_runs (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                command TEXT NOT NULL,
                screen_session TEXT,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(task_id) REFERENCES ssh_scheduled_tasks(id),
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_task_runs_task_time
                ON ssh_task_runs(task_id, started_at);

            CREATE TABLE IF NOT EXISTS ssh_terminal_tabs (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'native',
                screen_session TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                initial_command TEXT,
                tab_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(server_id) REFERENCES ssh_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssh_tabs_owner_order
                ON ssh_terminal_tabs(owner_user_id, tab_order);
            """
        )
        # Migration: add server_id to templates if missing
        try:
            connection.execute("SELECT server_id FROM ssh_command_templates LIMIT 1")
        except sqlite3.OperationalError:
            connection.execute("ALTER TABLE ssh_command_templates ADD COLUMN server_id TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ssh_templates_owner_server
                ON ssh_command_templates(owner_user_id, server_id, updated_at)
            """
        )
        # Migration: add initial_command to terminal tabs if missing
        try:
            connection.execute("SELECT initial_command FROM ssh_terminal_tabs LIMIT 1")
        except sqlite3.OperationalError:
            connection.execute("ALTER TABLE ssh_terminal_tabs ADD COLUMN initial_command TEXT")
            connection.execute(
                """
                UPDATE ssh_command_templates
                SET server_id = (
                    SELECT s.id FROM ssh_servers s
                    WHERE s.owner_user_id = ssh_command_templates.owner_user_id
                    AND s.enabled = 1
                    ORDER BY s.updated_at DESC LIMIT 1
                )
                WHERE server_id IS NULL
                """
            )
        # Migration: add pid column to ssh_screen_sessions if missing
        try:
            connection.execute("SELECT pid FROM ssh_screen_sessions LIMIT 1")
        except sqlite3.OperationalError:
            connection.execute("ALTER TABLE ssh_screen_sessions ADD COLUMN pid TEXT NOT NULL DEFAULT ''")


# ============================================================
# Servers
# ============================================================


def list_servers(user: User) -> list[dict[str, Any]]:
    init_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM ssh_servers WHERE owner_user_id = ? AND enabled = 1 ORDER BY name ASC",
            (user.id,),
        ).fetchall()
    return [_public_server(row) for row in rows]


def create_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    auth_type = _clean_auth_type(payload.get("authType", "password"))
    _validate_server_payload(payload, auth_type, creating=True)
    now = _now()
    server_id = _new_id()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ssh_servers (
                id, owner_user_id, name, host, port, ssh_username, auth_type,
                ssh_password_encrypted, private_key_encrypted, private_key_passphrase_encrypted,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id, user.id,
                _required(payload, "name"), _required(payload, "host"),
                int(payload.get("port") or 22), _required(payload, "sshUsername"),
                auth_type,
                _encrypt(payload.get("sshPassword") or "") if payload.get("sshPassword") else "",
                _encrypt(payload.get("privateKey") or "") if payload.get("privateKey") else "",
                _encrypt(payload.get("privateKeyPassphrase") or "") if payload.get("privateKeyPassphrase") else "",
                now, now,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM ssh_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def update_server(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    auth_type = _clean_auth_type(payload.get("authType", row["auth_type"]))
    _validate_server_payload(payload, auth_type, creating=False)
    password = payload.get("sshPassword")
    private_key = payload.get("privateKey")
    passphrase = payload.get("privateKeyPassphrase")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE ssh_servers
            SET name = ?, host = ?, port = ?, ssh_username = ?, auth_type = ?,
                ssh_password_encrypted = ?, private_key_encrypted = ?,
                private_key_passphrase_encrypted = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("name", row["name"]), payload.get("host", row["host"]),
                int(payload.get("port") or row["port"]),
                payload.get("sshUsername", row["ssh_username"]), auth_type,
                _encrypt(password) if password is not None and password != "" else row["ssh_password_encrypted"],
                _encrypt(private_key) if private_key is not None and private_key != "" else row["private_key_encrypted"],
                _encrypt(passphrase) if passphrase is not None and passphrase != "" else row["private_key_passphrase_encrypted"],
                _now(), server_id,
            ),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM ssh_servers WHERE id = ?", (server_id,)).fetchone()
    ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)
    return _public_server(updated)


def delete_server(server_id: str, user: User) -> None:
    get_server(server_id, user)
    now = _now()
    with get_connection() as connection:
        connection.execute("UPDATE ssh_servers SET enabled = 0, updated_at = ? WHERE id = ?", (now, server_id))
        connection.execute("UPDATE ssh_scheduled_tasks SET enabled = 0, updated_at = ? WHERE server_id = ? AND owner_user_id = ?", (now, server_id, user.id))
        connection.execute("DELETE FROM ssh_terminal_tabs WHERE server_id = ? AND owner_user_id = ?", (server_id, user.id))
        connection.commit()
    ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)


def copy_server(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    """Copy a server and all its templates + scheduled tasks."""
    row = get_server(server_id, user)
    now = _now()
    new_id = _new_id()
    new_name = (payload.get("name") or f"{row['name']} (副本)").strip()
    with get_connection() as connection:
        # Copy server record
        connection.execute(
            """
            INSERT INTO ssh_servers (
                id, owner_user_id, name, host, port, ssh_username, auth_type,
                ssh_password_encrypted, private_key_encrypted, private_key_passphrase_encrypted,
                has_screen, last_test_status, last_test_error, last_tested_at,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', '', NULL, 1, ?, ?)
            """,
            (
                new_id, user.id, new_name,
                row["host"], row["port"], row["ssh_username"], row["auth_type"],
                row["ssh_password_encrypted"], row["private_key_encrypted"], row["private_key_passphrase_encrypted"],
                row["has_screen"],
                now, now,
            ),
        )
        # Copy templates
        templates = connection.execute(
            "SELECT * FROM ssh_command_templates WHERE server_id = ? AND owner_user_id = ?",
            (server_id, user.id),
        ).fetchall()
        for t in templates:
            connection.execute(
                """
                INSERT INTO ssh_command_templates (id, owner_user_id, server_id, name, command, description, variables_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_new_id(), user.id, new_id, t["name"], t["command"], t["description"], t["variables_json"], now, now),
            )
        # Copy scheduled tasks (disabled by default)
        tasks = connection.execute(
            "SELECT * FROM ssh_scheduled_tasks WHERE server_id = ? AND owner_user_id = ?",
            (server_id, user.id),
        ).fetchall()
        for t in tasks:
            connection.execute(
                """
                INSERT INTO ssh_scheduled_tasks (id, owner_user_id, server_id, name, command, interval_seconds, screen_name_prefix, enabled, next_run_at, last_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?, ?)
                """,
                (
                    _new_id(), user.id, new_id, t["name"], t["command"], t["interval_seconds"], t["screen_name_prefix"],
                    (datetime.now(timezone.utc) + timedelta(seconds=t["interval_seconds"])).isoformat(),
                    now, now,
                ),
            )
        connection.commit()
        new_row = connection.execute("SELECT * FROM ssh_servers WHERE id = ?", (new_id,)).fetchone()
    return _public_server(new_row)


def get_server(server_id: str, user: User) -> sqlite3.Row:
    init_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM ssh_servers WHERE id = ? AND owner_user_id = ? AND enabled = 1",
            (server_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


def test_server(server_id: str, user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    now = _now()
    try:
        client = _ssh_connect(row, timeout=10)
        try:
            _ssh_exec(client, "whoami", timeout=8)
            screen_out, _, _ = _ssh_exec(client, "command -v screen >/dev/null 2>&1 && echo HAS_SCREEN || echo NO_SCREEN", timeout=8)
        finally:
            client.close()
        has_screen = "HAS_SCREEN" in screen_out
        status = "ok"
        error = ""
        result = {"connected": True, "hasScreen": has_screen}
    except Exception as exc:  # noqa: BLE001
        ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)
        has_screen = False
        status = "failed"
        error = str(exc)
        result = {"connected": False, "error": error, "hasScreen": False}
    with get_connection() as connection:
        connection.execute(
            "UPDATE ssh_servers SET has_screen = ?, last_test_status = ?, last_test_error = ?, last_tested_at = ?, updated_at = ? WHERE id = ?",
            (1 if has_screen else 0, status, error[:500], now, now, server_id),
        )
        connection.commit()
    return result


# ============================================================
# Screen sessions
# ============================================================


def list_screen_sessions(server_id: str, user: User, refresh: bool = True) -> list[dict[str, Any]]:
    server = get_server(server_id, user)
    _require_screen(server)
    if refresh:
        _refresh_screen_rows(server, user)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM ssh_screen_sessions WHERE owner_user_id = ? AND server_id = ? ORDER BY started_at DESC",
            (user.id, server_id),
        ).fetchall()
    return [_public_screen_session(row) for row in rows]


def create_screen_session(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    server = get_server(server_id, user)
    _require_screen(server)
    name = _safe_session_name(payload.get("name") or f"ssh_{int(time.time())}")
    command = payload.get("command") or ""
    if command.strip():
        shell_cmd = f"screen -dmS {shlex.quote(name)} bash -lc {shlex.quote(command + '; exec bash')}"
    else:
        shell_cmd = f"screen -dmS {shlex.quote(name)} bash"
    _run_ssh(server, shell_cmd, timeout=15)
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO ssh_screen_sessions (id, owner_user_id, server_id, session_name, status, created_by_tool, command, started_at, checked_at)
            VALUES (COALESCE((SELECT id FROM ssh_screen_sessions WHERE owner_user_id=? AND server_id=? AND session_name=?), ?), ?, ?, ?, 'running', 1, ?, ?, ?)
            """,
            (user.id, server_id, name, _new_id(), user.id, server_id, name, command, now, now),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM ssh_screen_sessions WHERE owner_user_id=? AND server_id=? AND session_name=?",
            (user.id, server_id, name),
        ).fetchone()
    return _public_screen_session(row)


def rename_screen_session(server_id: str, session_name: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    server = get_server(server_id, user)
    _require_screen(server)
    old_name = _safe_session_name(session_name)
    new_name = _safe_session_name(_required(payload, "name"))
    _run_ssh(server, f"screen -S {shlex.quote(old_name)} -X sessionname {shlex.quote(new_name)}", timeout=10)
    with get_connection() as connection:
        now = _now()
        connection.execute(
            "UPDATE ssh_screen_sessions SET session_name = ?, checked_at = ?, status = 'running' WHERE owner_user_id = ? AND server_id = ? AND session_name = ?",
            (new_name, now, user.id, server_id, old_name),
        )
        if connection.total_changes == 0:
            connection.execute(
                "INSERT INTO ssh_screen_sessions (id, owner_user_id, server_id, session_name, status, created_by_tool, started_at, checked_at) VALUES (?, ?, ?, ?, 'running', 0, ?, ?)",
                (_new_id(), user.id, server_id, new_name, now, now),
            )
        connection.commit()
    return {"sessionName": new_name, "renamed": True}


def delete_screen_session(server_id: str, session_name: str, user: User) -> None:
    server = get_server(server_id, user)
    _require_screen(server)
    clean_name = _safe_session_name(session_name)
    _run_ssh(server, f"screen -S {shlex.quote(clean_name)} -X quit", timeout=10)
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM ssh_screen_sessions WHERE owner_user_id = ? AND server_id = ? AND session_name = ?",
            (user.id, server_id, clean_name),
        )
        connection.commit()


# ============================================================
# Command history
# ============================================================


def list_history(user: User, server_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_database()
    limit = max(1, min(limit, 300))
    params: list[Any] = [user.id]
    where = "owner_user_id = ?"
    if server_id:
        get_server(server_id, user)
        where += " AND server_id = ?"
        params.append(server_id)
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM ssh_command_history WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_public_history(row) for row in rows]


def record_history(payload: dict[str, Any], user: User) -> dict[str, Any]:
    server_id = payload.get("serverId")
    if server_id:
        get_server(server_id, user)
    command = _required(payload, "command")
    with get_connection() as connection:
        history_id = _new_id()
        connection.execute(
            "INSERT INTO ssh_command_history (id, owner_user_id, server_id, source, command, exit_status, screen_session, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (history_id, user.id, server_id, payload.get("source") or "terminal", command, payload.get("exitStatus"), payload.get("screenSession"), _now()),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM ssh_command_history WHERE id = ?", (history_id,)).fetchone()
    return _public_history(row)


# ============================================================
# Templates (bound to server)
# ============================================================


def list_templates(server_id: str, user: User) -> list[dict[str, Any]]:
    init_database()
    get_server(server_id, user)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM ssh_command_templates WHERE owner_user_id = ? AND server_id = ? ORDER BY updated_at DESC",
            (user.id, server_id),
        ).fetchall()
    return [_public_template(row) for row in rows]


def create_template(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    server_id = _required(payload, "serverId")
    get_server(server_id, user)
    now = _now()
    template_id = _new_id()
    variables = payload.get("variables") or _detect_variables(payload.get("command") or "")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO ssh_command_templates (id, owner_user_id, server_id, name, command, description, variables_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (template_id, user.id, server_id, _required(payload, "name"), _required(payload, "command"), payload.get("description") or "", json.dumps(_clean_variables(variables), ensure_ascii=False), now, now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM ssh_command_templates WHERE id = ?", (template_id,)).fetchone()
    return _public_template(row)


def update_template(template_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = _get_template(template_id, user)
    command = payload.get("command", row["command"])
    variables = payload.get("variables")
    if variables is None and "command" in payload:
        variables = _detect_variables(command)
    elif variables is None:
        variables = json.loads(row["variables_json"])
    with get_connection() as connection:
        connection.execute(
            "UPDATE ssh_command_templates SET name = ?, command = ?, description = ?, variables_json = ?, updated_at = ? WHERE id = ?",
            (payload.get("name", row["name"]), command, payload.get("description", row["description"]), json.dumps(_clean_variables(variables), ensure_ascii=False), _now(), template_id),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM ssh_command_templates WHERE id = ?", (template_id,)).fetchone()
    return _public_template(updated)


def delete_template(template_id: str, user: User) -> None:
    _get_template(template_id, user)
    with get_connection() as connection:
        connection.execute("DELETE FROM ssh_command_templates WHERE id = ?", (template_id,))
        connection.commit()


# ============================================================
# Scheduled tasks (bound to server)
# ============================================================


def list_scheduled_tasks(user: User, server_id: str | None = None) -> list[dict[str, Any]]:
    init_database()
    params: list[Any] = [user.id]
    where = "owner_user_id = ?"
    if server_id:
        get_server(server_id, user)
        where += " AND server_id = ?"
        params.append(server_id)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM ssh_scheduled_tasks WHERE {where} ORDER BY enabled DESC, updated_at DESC",
            params,
        ).fetchall()
    return [_public_task(row) for row in rows]


def create_scheduled_task(payload: dict[str, Any], user: User) -> dict[str, Any]:
    server = get_server(_required(payload, "serverId"), user)
    _require_screen(server)
    interval = _clean_interval(payload.get("intervalSeconds"))
    now = _now_dt()
    task_id = _new_id()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO ssh_scheduled_tasks (id, owner_user_id, server_id, name, command, interval_seconds, screen_name_prefix, enabled, next_run_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, user.id, server["id"], _required(payload, "name"), _required(payload, "command"), interval, payload.get("screenNamePrefix") or "", 1 if payload.get("enabled", True) else 0, (now + timedelta(seconds=interval)).isoformat(), now.isoformat(), now.isoformat()),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM ssh_scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return _public_task(row)


def update_scheduled_task(task_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = _get_task(task_id, user)
    server_id = payload.get("serverId", row["server_id"])
    server = get_server(server_id, user)
    _require_screen(server)
    interval = _clean_interval(payload.get("intervalSeconds", row["interval_seconds"]))
    enabled = bool(payload.get("enabled", bool(row["enabled"])))
    now = _now_dt()
    next_run_at = row["next_run_at"]
    if interval != row["interval_seconds"] or enabled != bool(row["enabled"]):
        next_run_at = (now + timedelta(seconds=interval)).isoformat()
    with get_connection() as connection:
        connection.execute(
            "UPDATE ssh_scheduled_tasks SET server_id = ?, name = ?, command = ?, interval_seconds = ?, screen_name_prefix = ?, enabled = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
            (server_id, payload.get("name", row["name"]), payload.get("command", row["command"]), interval, payload.get("screenNamePrefix", row["screen_name_prefix"]), 1 if enabled else 0, next_run_at, now.isoformat(), task_id),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM ssh_scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return _public_task(updated)


def delete_scheduled_task(task_id: str, user: User) -> None:
    _get_task(task_id, user)
    with get_connection() as connection:
        connection.execute("DELETE FROM ssh_scheduled_tasks WHERE id = ?", (task_id,))
        connection.commit()


def list_task_runs(task_id: str, user: User, limit: int = 50) -> list[dict[str, Any]]:
    _get_task(task_id, user)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM ssh_task_runs WHERE owner_user_id = ? AND task_id = ? ORDER BY started_at DESC LIMIT ?",
            (user.id, task_id, max(1, min(limit, 200))),
        ).fetchall()
    return [_public_run(row) for row in rows]


def collect_due_tasks() -> None:
    init_database()
    now = _now()
    with get_connection() as connection:
        tasks = connection.execute(
            "SELECT * FROM ssh_scheduled_tasks WHERE enabled = 1 AND next_run_at <= ? ORDER BY next_run_at ASC LIMIT 20",
            (now,),
        ).fetchall()
    for task in tasks:
        _run_due_task(task)


# ============================================================
# Terminal tabs (persistence)
# ============================================================


def list_terminal_tabs(user: User) -> list[dict[str, Any]]:
    init_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM ssh_terminal_tabs WHERE owner_user_id = ? ORDER BY tab_order ASC",
            (user.id,),
        ).fetchall()
    return [_public_tab(row) for row in rows]


def save_terminal_tabs(tabs: list[dict[str, Any]], user: User) -> list[dict[str, Any]]:
    """Replace all terminal tabs for this user."""
    init_database()
    now = _now()
    with get_connection() as connection:
        connection.execute("DELETE FROM ssh_terminal_tabs WHERE owner_user_id = ?", (user.id,))
        for idx, tab in enumerate(tabs):
            server_id = tab.get("serverId", "")
            if not server_id:
                continue
            # Verify server belongs to user
            srv = connection.execute(
                "SELECT id FROM ssh_servers WHERE id = ? AND owner_user_id = ? AND enabled = 1",
                (server_id, user.id),
            ).fetchone()
            if srv is None:
                continue
            tab_id = tab.get("id") or _new_id()
            connection.execute(
                "INSERT INTO ssh_terminal_tabs (id, owner_user_id, server_id, mode, screen_session, label, initial_command, tab_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tab_id, user.id, server_id, tab.get("mode") or "native", tab.get("screenSession") or "", tab.get("label") or "", tab.get("initialCommand") or None, idx, now, now),
            )
        connection.commit()
        rows = connection.execute(
            "SELECT * FROM ssh_terminal_tabs WHERE owner_user_id = ? ORDER BY tab_order ASC",
            (user.id,),
        ).fetchall()
    return [_public_tab(row) for row in rows]


# ============================================================
# Terminal WebSocket
# ============================================================


async def terminal_websocket(
    websocket: WebSocket,
    server_id: str,
    mode: str = "native",
    screen_session: str | None = None,
    cols: int = 80,
    rows: int = 24,
    initial_command: str | None = None,
) -> None:
    # Accept first so we can send structured error messages to the client
    await websocket.accept()

    token = websocket.cookies.get(get_settings().session_cookie_name)
    user = get_user_by_session_token(token)
    if user is None:
        await websocket.send_json({"type": "error", "message": "未登录或登录已过期，请刷新页面重新登录"})
        await websocket.close(code=4401)
        return
    try:
        server = get_server(server_id, user)
        if mode != "native":
            _require_screen(server)
    except ToolboxError as exc:
        await websocket.send_json({"type": "error", "message": exc.message})
        await websocket.close(code=4404)
        return

    client = None
    channel = None
    try:
        # Connect SSH and open an interactive shell with proper dimensions
        client = await asyncio.to_thread(_ssh_connect, server, 20)
        cols = max(20, min(int(cols) or 80, 400))
        rows = max(5, min(int(rows) or 24, 120))
        channel = await asyncio.to_thread(
            client.invoke_shell,
            "xterm-256color",
            cols,
            rows,
        )
        channel.settimeout(0.0)
        await websocket.send_json({"type": "status", "status": "connected"})

        # If screen mode, send the appropriate screen command
        if mode == "screen_existing" and screen_session:
            safe_name = _safe_session_name(screen_session)
            # Resolve the pid to avoid ambiguity when multiple sessions share the same name.
            # Use exec_command (not the shell channel) to get screen -ls output reliably.
            resolved_target = await asyncio.to_thread(_resolve_screen_target, client, safe_name)
            if resolved_target:
                # Use the full "pid.name" form to avoid "several suitable screens" error
                await asyncio.to_thread(channel.send, f"screen -dR {shlex.quote(resolved_target)}\n")
            else:
                # Session not found — let screen create it or show the error to the user
                await asyncio.to_thread(channel.send, f"screen -dR {shlex.quote(safe_name)}\n")
        elif mode == "screen_new" and screen_session:
            safe_name = _safe_session_name(screen_session)
            await asyncio.to_thread(channel.send, f"screen -S {shlex.quote(safe_name)}\n")

        # Send initial command if provided
        if initial_command:
            # Small delay to let the shell/screen settle before sending the command
            await asyncio.sleep(0.5)
            await asyncio.to_thread(channel.send, initial_command + "\n")

        async def read_ssh() -> None:
            while True:
                try:
                    if channel.recv_ready():
                        data = await asyncio.to_thread(channel.recv, 32768)
                        if data:
                            await websocket.send_json({"type": "output", "data": data.decode("utf-8", errors="replace")})
                    if channel.recv_stderr_ready():
                        data = await asyncio.to_thread(channel.recv_stderr, 32768)
                        if data:
                            await websocket.send_json({"type": "output", "data": data.decode("utf-8", errors="replace")})
                    if channel.exit_status_ready():
                        await websocket.send_json({"type": "status", "status": "closed"})
                        break
                except Exception:
                    break
                await asyncio.sleep(0.02)

        async def read_ws() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
                if "text" in message and message["text"] is not None:
                    text = message["text"]
                    if text.startswith("{"):
                        handled = await _handle_terminal_control(channel, text)
                        if handled:
                            continue
                    await asyncio.to_thread(channel.send, text)
                elif "bytes" in message and message["bytes"] is not None:
                    await asyncio.to_thread(channel.send, message["bytes"])

        tasks = [asyncio.create_task(read_ssh()), asyncio.create_task(read_ws())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


async def _handle_terminal_control(channel: Any, text: str) -> bool:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    msg_type = payload.get("type")
    if msg_type == "resize":
        cols = int(payload.get("cols") or 80)
        rows = int(payload.get("rows") or 24)
        await asyncio.to_thread(channel.resize_pty, width=max(20, cols), height=max(5, rows))
        return True
    if msg_type == "ping":
        return True
    if msg_type == "input":
        await asyncio.to_thread(channel.send, payload.get("data") or "")
        return True
    return False


# ============================================================
# Scheduler
# ============================================================


def _run_due_task(task: sqlite3.Row) -> None:
    started = _now()
    run_id = _new_id()
    status = "started"
    error = ""
    screen_session = _safe_session_name(f"{task['screen_name_prefix'] or 'ssh_task'}_{int(time.time())}_{task['id'][:6]}")
    server = None
    try:
        with get_connection() as connection:
            server = connection.execute(
                "SELECT * FROM ssh_servers WHERE id = ? AND owner_user_id = ? AND enabled = 1",
                (task["server_id"], task["owner_user_id"]),
            ).fetchone()
        if server is None:
            raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或不可访问", status_code=404, tool_id=TOOL_ID)
        _require_screen(server)
        remote_cmd = f"screen -dmS {shlex.quote(screen_session)} bash -lc {shlex.quote(task['command'])}"
        _run_ssh(server, remote_cmd, timeout=20)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = str(exc)[:500]
        screen_session = ""
    finished = _now()
    next_run_at = (_now_dt() + timedelta(seconds=max(60, int(task["interval_seconds"])))).isoformat()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO ssh_task_runs (id, owner_user_id, task_id, server_id, command, screen_session, status, error, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, task["owner_user_id"], task["id"], task["server_id"], task["command"], screen_session, status, error, started, finished),
        )
        connection.execute(
            "UPDATE ssh_scheduled_tasks SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
            (started, next_run_at, finished, task["id"]),
        )
        if status == "started" and server is not None:
            connection.execute(
                "INSERT OR REPLACE INTO ssh_screen_sessions (id, owner_user_id, server_id, session_name, status, created_by_tool, command, started_at, checked_at) VALUES (COALESCE((SELECT id FROM ssh_screen_sessions WHERE owner_user_id=? AND server_id=? AND session_name=?), ?), ?, ?, ?, 'running', 1, ?, ?, ?)",
                (task["owner_user_id"], task["server_id"], screen_session, _new_id(), task["owner_user_id"], task["server_id"], screen_session, task["command"], started, finished),
            )
            connection.execute(
                "INSERT INTO ssh_command_history (id, owner_user_id, server_id, source, command, screen_session, created_at) VALUES (?, ?, ?, 'scheduled_task', ?, ?, ?)",
                (_new_id(), task["owner_user_id"], task["server_id"], task["command"], screen_session, started),
            )
        connection.commit()


def _refresh_screen_rows(server: sqlite3.Row, user: User) -> None:
    output = _run_ssh(server, "screen -ls 2>/dev/null || true", timeout=10)
    parsed = parse_screen_ls(output)
    now = _now()
    alive = {item["sessionName"]: item for item in parsed}
    with get_connection() as connection:
        for item in parsed:
            connection.execute(
                "INSERT OR IGNORE INTO ssh_screen_sessions (id, owner_user_id, server_id, session_name, pid, status, created_by_tool, started_at, checked_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (_new_id(), user.id, server["id"], item["sessionName"], item.get("pid", ""), item["status"], now, now),
            )
            connection.execute(
                "UPDATE ssh_screen_sessions SET status = ?, pid = ?, checked_at = ? WHERE owner_user_id = ? AND server_id = ? AND session_name = ?",
                (item["status"], item.get("pid", ""), now, user.id, server["id"], item["sessionName"]),
            )
        existing = connection.execute(
            "SELECT * FROM ssh_screen_sessions WHERE owner_user_id = ? AND server_id = ? AND status = 'running'",
            (user.id, server["id"]),
        ).fetchall()
        for row in existing:
            if row["session_name"] not in alive:
                connection.execute("UPDATE ssh_screen_sessions SET status = 'done', checked_at = ? WHERE id = ?", (now, row["id"]))
        connection.commit()


def parse_screen_ls(output: str) -> list[dict[str, str]]:
    sessions: list[dict[str, str]] = []
    for line in output.splitlines():
        match = _SESSION_RE.match(line)
        if not match:
            continue
        state = match.group("state").lower()
        status = "running" if "detached" in state or "attached" in state else "unknown"
        sessions.append({
            "sessionName": match.group("name"),
            "pid": match.group("pid"),
            "status": status,
            "state": match.group("state"),
        })
    return sessions


def _resolve_screen_target(client: Any, session_name: str) -> str:
    """Run `screen -ls` via exec_command and return the full 'pid.name' identifier
    for the given session name. If multiple sessions share the name, returns the
    first (most recently listed) one. Returns empty string if not found."""
    try:
        out, _, _ = _ssh_exec(client, "screen -ls 2>/dev/null || true", timeout=8)
    except Exception:
        return ""
    parsed = parse_screen_ls(out)
    for item in parsed:
        if item["sessionName"] == session_name and item.get("pid"):
            return f"{item['pid']}.{item['sessionName']}"
    return ""


# ============================================================
# SSH helpers
# ============================================================


def _ssh_connect(row: sqlite3.Row | Any, timeout: int = 20):
    return ssh_connection_service.borrow_client(_ssh_spec(row, timeout=timeout))


def _load_private_key(row: sqlite3.Row, paramiko: Any) -> Any:
    key_text = _decrypt(row["private_key_encrypted"])
    passphrase = _decrypt(row["private_key_passphrase_encrypted"]) if row["private_key_passphrase_encrypted"] else None
    key_errors: list[str] = []
    for key_cls_name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
        key_cls = getattr(paramiko, key_cls_name, None)
        if key_cls is None:
            continue
        try:
            return key_cls.from_private_key(io.StringIO(key_text), password=passphrase)
        except Exception as exc:  # noqa: BLE001
            key_errors.append(f"{key_cls_name}: {exc}")
    raise ToolboxError("PRIVATE_KEY_INVALID", "私钥无法解析或 passphrase 不正确", status_code=400, tool_id=TOOL_ID, extra={"details": key_errors[-2:]})


def _ssh_exec(client: Any, command: str, timeout: int = 30) -> tuple[str, str, int]:
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return out, err, code
    except Exception:
        if hasattr(client, "invalidate"):
            client.invalidate()
        raise


def _run_ssh(row: sqlite3.Row, command: str, timeout: int = 30) -> str:
    out, err, code = ssh_connection_service.exec_command(_ssh_spec(row, timeout=timeout), command, timeout=timeout)
    if code != 0 and err.strip():
        raise ToolboxError("SSH_COMMAND_FAILED", err.strip()[:500], status_code=502, tool_id=TOOL_ID)
    return out


def _ssh_spec(row: sqlite3.Row | Any, timeout: int = 20) -> SSHConnectionSpec:
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖，无法执行 SSH 命令", status_code=500, tool_id=TOOL_ID) from exc

    auth_type = row["auth_type"]
    if auth_type == "private_key":
        return SSHConnectionSpec(
            tool_id=TOOL_ID,
            server_id=row["id"],
            host=row["host"],
            port=int(row["port"]),
            username=row["ssh_username"],
            auth_fingerprint=ssh_connection_service.auth_fingerprint(
                auth_type,
                row["private_key_encrypted"],
                row["private_key_passphrase_encrypted"],
            ),
            pkey=_load_private_key(row, paramiko),
            connect_timeout=timeout,
            connect_error_code="SSH_CONNECT_FAILED",
            missing_dependency_code="SSH_DEPENDENCY_MISSING",
            missing_dependency_message="缺少 paramiko 依赖，无法执行 SSH 命令",
        )
    return SSHConnectionSpec(
        tool_id=TOOL_ID,
        server_id=row["id"],
        host=row["host"],
        port=int(row["port"]),
        username=row["ssh_username"],
        auth_fingerprint=ssh_connection_service.auth_fingerprint(auth_type, row["ssh_password_encrypted"]),
        password=_decrypt(row["ssh_password_encrypted"]),
        connect_timeout=timeout,
        connect_error_code="SSH_CONNECT_FAILED",
        missing_dependency_code="SSH_DEPENDENCY_MISSING",
        missing_dependency_message="缺少 paramiko 依赖，无法执行 SSH 命令",
    )


def _require_screen(server: sqlite3.Row) -> None:
    if not bool(server["has_screen"]):
        raise ToolboxError("SCREEN_UNAVAILABLE", "该服务器未检测到 screen，无法使用后台会话或定时任务", status_code=400, tool_id=TOOL_ID)


def _get_template(template_id: str, user: User) -> sqlite3.Row:
    init_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM ssh_command_templates WHERE id = ? AND owner_user_id = ?",
            (template_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("TEMPLATE_NOT_FOUND", "命令模板不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


def _get_task(task_id: str, user: User) -> sqlite3.Row:
    init_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM ssh_scheduled_tasks WHERE id = ? AND owner_user_id = ?",
            (task_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("TASK_NOT_FOUND", "定时任务不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


# ============================================================
# Public serializers
# ============================================================


def _public_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
        "sshUsername": row["ssh_username"], "authType": row["auth_type"],
        "hasScreen": bool(row["has_screen"]),
        "lastTestStatus": row["last_test_status"], "lastTestError": row["last_test_error"],
        "lastTestedAt": row["last_tested_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _public_screen_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "serverId": row["server_id"], "sessionName": row["session_name"],
        "pid": row["pid"] if "pid" in row.keys() else "",
        "status": row["status"], "createdByTool": bool(row["created_by_tool"]),
        "command": row["command"], "startedAt": row["started_at"], "checkedAt": row["checked_at"],
    }


def _public_history(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "serverId": row["server_id"], "source": row["source"],
        "command": row["command"], "exitStatus": row["exit_status"],
        "screenSession": row["screen_session"], "createdAt": row["created_at"],
    }


def _public_template(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "serverId": row["server_id"], "name": row["name"],
        "command": row["command"], "description": row["description"],
        "variables": json.loads(row["variables_json"] or "[]"),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _public_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "serverId": row["server_id"], "name": row["name"],
        "command": row["command"], "intervalSeconds": row["interval_seconds"],
        "screenNamePrefix": row["screen_name_prefix"], "enabled": bool(row["enabled"]),
        "nextRunAt": row["next_run_at"], "lastRunAt": row["last_run_at"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _public_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "taskId": row["task_id"], "serverId": row["server_id"],
        "command": row["command"], "screenSession": row["screen_session"],
        "status": row["status"], "error": row["error"],
        "startedAt": row["started_at"], "finishedAt": row["finished_at"],
    }


def _public_tab(row: sqlite3.Row) -> dict[str, Any]:
    result = {
        "id": row["id"], "serverId": row["server_id"], "mode": row["mode"],
        "screenSession": row["screen_session"], "label": row["label"],
        "tabOrder": row["tab_order"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }
    try:
        result["initialCommand"] = row["initial_command"]
    except (IndexError, KeyError):
        result["initialCommand"] = None
    return result


# ============================================================
# Misc helpers
# ============================================================


def _get_fernet() -> Fernet:
    secret = get_settings().session_secret
    key = base64.urlsafe_b64encode(secret.encode("utf-8").ljust(32)[:32])
    return Fernet(key)


def _encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("DECRYPT_ERROR", "凭证解密失败", status_code=500, tool_id=TOOL_ID) from exc


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _new_id() -> str:
    return secrets.token_hex(12)


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ToolboxError("INVALID_PAYLOAD", f"{key} 不能为空", status_code=400, tool_id=TOOL_ID)
    return value


def _clean_auth_type(value: Any) -> str:
    auth_type = str(value or "password")
    if auth_type not in {"password", "private_key"}:
        raise ToolboxError("INVALID_AUTH_TYPE", "认证方式不合法", status_code=400, tool_id=TOOL_ID)
    return auth_type


def _validate_server_payload(payload: dict[str, Any], auth_type: str, creating: bool) -> None:
    for key in ("name", "host", "sshUsername"):
        if creating or key in payload:
            _required(payload, key)
    if auth_type == "password" and creating and not payload.get("sshPassword"):
        raise ToolboxError("PASSWORD_REQUIRED", "密码登录需要填写 SSH 密码", status_code=400, tool_id=TOOL_ID)
    if auth_type == "private_key" and creating and not payload.get("privateKey"):
        raise ToolboxError("PRIVATE_KEY_REQUIRED", "私钥登录需要填写私钥内容", status_code=400, tool_id=TOOL_ID)


def _safe_session_name(name: str) -> str:
    clean = _SAFE_SESSION_RE.sub("_", str(name).strip()).strip("._-")
    if not clean:
        raise ToolboxError("INVALID_SESSION_NAME", "screen 会话名不能为空", status_code=400, tool_id=TOOL_ID)
    return clean[:80]


def _detect_variables(command: str) -> list[str]:
    return sorted(set(re.findall(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}", command)))


def _clean_variables(variables: Any) -> list[str]:
    if not isinstance(variables, list):
        return []
    cleaned: list[str] = []
    for item in variables:
        value = str(item).strip()
        if value and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value) and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _clean_interval(value: Any) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolboxError("INVALID_INTERVAL", "定时间隔不合法", status_code=400, tool_id=TOOL_ID) from exc
    if interval < 60:
        raise ToolboxError("INVALID_INTERVAL", "定时间隔不能小于 60 秒", status_code=400, tool_id=TOOL_ID)
    return interval
