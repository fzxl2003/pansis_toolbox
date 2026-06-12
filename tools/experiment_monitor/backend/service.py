from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection
from backend.app.services.auth_service import User

logger = logging.getLogger(__name__)

TOOL_ID = "experiment_monitor"
CHECK_INTERVAL_SECONDS = 30
RETENTION_DAYS = 7
DEFAULT_CONFIRM_COUNT = 3


# ============================================================
# Database Initialization
# ============================================================

def init_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            -- 服务器连接配置（复用 server_monitor 的模式）
            CREATE TABLE IF NOT EXISTS em_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                ssh_username TEXT NOT NULL,
                ssh_password_encrypted TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- 监控任务
            CREATE TABLE IF NOT EXISTS em_monitor_tasks (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                -- 进程匹配模式: 'simple' | 'regex'
                match_mode TEXT NOT NULL DEFAULT 'simple',
                -- 匹配字符串（simple 模式下是子串，regex 模式下是正则）
                match_pattern TEXT NOT NULL,
                -- 监控哪个用户的进程，空字符串表示所有用户
                filter_user TEXT NOT NULL DEFAULT '',
                -- 报警条件类型: 'below' | 'above' | 'changed'
                alert_condition TEXT NOT NULL DEFAULT 'below',
                -- 报警阈值（below/above 用）
                alert_threshold INTEGER NOT NULL DEFAULT 0,
                -- 变动阈值（changed 模式用，绝对值）
                alert_change_amount INTEGER NOT NULL DEFAULT 1,
                -- 连续确认次数
                confirm_count INTEGER NOT NULL DEFAULT 3,
                -- 检查间隔秒数
                check_interval_seconds INTEGER NOT NULL DEFAULT 30,
                -- 是否启用
                enabled INTEGER NOT NULL DEFAULT 1,
                -- 上次确认的进程数（用于 changed 模式）
                last_confirmed_count INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(server_id) REFERENCES em_servers(id)
            );

            -- 报警动作：邮件通知
            CREATE TABLE IF NOT EXISTS em_alert_actions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                action_type TEXT NOT NULL,  -- 'email' | 'script'
                -- 邮件相关
                email_recipients TEXT NOT NULL DEFAULT '[]',  -- JSON array
                email_subject_template TEXT NOT NULL DEFAULT '实验监控报警: {task_name}',
                email_body_template TEXT NOT NULL DEFAULT '监控任务 "{task_name}" 触发报警条件。\n服务器: {server_name}\n当前进程数: {current_count}\n阈值: {threshold}\n时间: {time}',
                -- 脚本执行相关
                script_commands TEXT NOT NULL DEFAULT '[]',  -- JSON array of commands
                script_screen_name TEXT DEFAULT '',  -- screen 会话名前缀
                scripts_per_trigger INTEGER NOT NULL DEFAULT 1,  -- 每次触发执行几个脚本
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES em_monitor_tasks(id)
            );

            -- 监控采样记录
            CREATE TABLE IF NOT EXISTS em_samples (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                process_count INTEGER NOT NULL DEFAULT 0,
                matched_processes TEXT NOT NULL DEFAULT '[]',  -- JSON array of process info strings
                condition_met INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                FOREIGN KEY(task_id) REFERENCES em_monitor_tasks(id)
            );

            -- 报警事件记录
            CREATE TABLE IF NOT EXISTS em_alert_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                action_id TEXT,
                event_type TEXT NOT NULL,  -- 'triggered' | 'email_sent' | 'script_executed' | 'resolved'
                message TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES em_monitor_tasks(id),
                FOREIGN KEY(action_id) REFERENCES em_alert_actions(id)
            );

            -- 报警状态追踪（用于多次确认机制）
            CREATE TABLE IF NOT EXISTS em_alert_states (
                task_id TEXT PRIMARY KEY,
                consecutive_meets INTEGER NOT NULL DEFAULT 0,
                last_check_count INTEGER,
                -- 上次采样到的进程列表（JSON 数组），用于邮件模板 {prev_processes}
                last_matched_processes TEXT NOT NULL DEFAULT '[]',
                -- 触发报警前一刻记录的进程列表，用于邮件模板 {prev_processes}
                baseline_processes TEXT NOT NULL DEFAULT '[]',
                is_alerting INTEGER NOT NULL DEFAULT 0,
                last_alerted_at TEXT,
                resolved_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES em_monitor_tasks(id)
            );

            -- 全局邮件配置（仅管理员可配置）
            CREATE TABLE IF NOT EXISTS em_email_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_em_samples_task_time
                ON em_samples(task_id, checked_at);
            CREATE INDEX IF NOT EXISTS idx_em_alert_events_task
                ON em_alert_events(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_em_monitor_tasks_server
                ON em_monitor_tasks(server_id);
            """
        )
        # 迁移：为旧表补充新字段（ALTER TABLE IF NOT EXISTS column 仅 SQLite 3.37+ 支持，用 try/except 兼容旧版本）
        for col_sql in [
            "ALTER TABLE em_alert_states ADD COLUMN last_matched_processes TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE em_alert_states ADD COLUMN baseline_processes TEXT NOT NULL DEFAULT '[]'",
        ]:
            try:
                connection.execute(col_sql)
                connection.commit()
            except Exception:
                pass  # 字段已存在，忽略


# ============================================================
# Server Management (SSH Connections)
# ============================================================

def list_servers(user: User) -> list[dict[str, Any]]:
    init_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM em_servers WHERE owner_user_id = ? AND enabled = 1 ORDER BY name",
            (user.id,),
        ).fetchall()
    return [_public_server(row) for row in rows]


def get_server(server_id: str, user: User) -> sqlite3.Row:
    init_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM em_servers WHERE id = ? AND owner_user_id = ? AND enabled = 1",
            (server_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


def create_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    server_id = secrets.token_hex(12)
    now = now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO em_servers (id, name, host, port, ssh_username, ssh_password_encrypted, owner_user_id, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                server_id,
                _required(payload, "name"),
                _required(payload, "host"),
                int(payload.get("port") or 22),
                _required(payload, "sshUsername"),
                encrypt_secret(_required(payload, "sshPassword")),
                user.id,
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM em_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def update_server(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    password = payload.get("sshPassword")
    encrypted_password = encrypt_secret(password) if password else row["ssh_password_encrypted"]
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE em_servers SET name = ?, host = ?, port = ?, ssh_username = ?, ssh_password_encrypted = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("name", row["name"]),
                payload.get("host", row["host"]),
                int(payload.get("port") or row["port"]),
                payload.get("sshUsername", row["ssh_username"]),
                encrypted_password,
                now_iso(),
                server_id,
            ),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM em_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(updated)


def delete_server(server_id: str, user: User) -> None:
    get_server(server_id, user)
    with get_connection() as connection:
        connection.execute("UPDATE em_servers SET enabled = 0, updated_at = ? WHERE id = ?", (now_iso(), server_id))
        # Also disable related tasks
        connection.execute(
            "UPDATE em_monitor_tasks SET enabled = 0, updated_at = ? WHERE server_id = ?",
            (now_iso(), server_id),
        )
        connection.commit()


def test_ssh_connection(server_id: str, user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    try:
        output = _run_ssh(row, "echo 'connection_ok' && whoami", timeout=10)
        # Check if server has screen
        screen_output = _run_ssh(row, "which screen >/dev/null 2>&1 && echo 'screen_available' || echo 'screen_unavailable'", timeout=5)
        has_screen = "screen_available" in screen_output
        return {
            "connected": True,
            "username": output.strip().split("\n")[-1].strip(),
            "hasScreen": has_screen,
        }
    except Exception as exc:
        return {"connected": False, "error": str(exc), "hasScreen": False}


# ============================================================
# Monitor Task Management
# ============================================================

def list_monitor_tasks(user: User, server_id: str | None = None) -> list[dict[str, Any]]:
    init_database()
    with get_connection() as connection:
        if server_id:
            rows = connection.execute(
                "SELECT * FROM em_monitor_tasks WHERE owner_user_id = ? AND server_id = ? ORDER BY created_at DESC",
                (user.id, server_id),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM em_monitor_tasks WHERE owner_user_id = ? ORDER BY created_at DESC",
                (user.id,),
            ).fetchall()
    return [_public_task(row) for row in rows]


def get_monitor_task(task_id: str, user: User) -> sqlite3.Row:
    init_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM em_monitor_tasks WHERE id = ? AND owner_user_id = ?",
            (task_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("TASK_NOT_FOUND", "监控任务不存在", status_code=404, tool_id=TOOL_ID)
    return row


def create_monitor_task(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    # Validate server belongs to user
    get_server(payload["serverId"], user)

    task_id = secrets.token_hex(12)
    now = now_iso()
    match_mode = payload.get("matchMode", "simple")
    if match_mode not in ("simple", "regex"):
        raise ToolboxError("INVALID_MATCH_MODE", "匹配模式必须是 simple 或 regex", status_code=400, tool_id=TOOL_ID)
    alert_condition = payload.get("alertCondition", "below")
    if alert_condition not in ("below", "above", "changed"):
        raise ToolboxError("INVALID_ALERT_CONDITION", "报警条件类型无效", status_code=400, tool_id=TOOL_ID)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO em_monitor_tasks (
                id, server_id, owner_user_id, name, description, match_mode, match_pattern,
                filter_user, alert_condition, alert_threshold, alert_change_amount,
                confirm_count, check_interval_seconds, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                task_id,
                payload["serverId"],
                user.id,
                _required(payload, "name"),
                payload.get("description", ""),
                match_mode,
                _required(payload, "matchPattern"),
                payload.get("filterUser", ""),
                alert_condition,
                int(payload.get("alertThreshold", 0)),
                int(payload.get("alertChangeAmount", 1)),
                int(payload.get("confirmCount", DEFAULT_CONFIRM_COUNT)),
                int(payload.get("checkIntervalSeconds", CHECK_INTERVAL_SECONDS)),
                now,
                now,
            ),
        )
        # Initialize alert state
        connection.execute(
            """
            INSERT OR IGNORE INTO em_alert_states (task_id, consecutive_meets, is_alerting, updated_at)
            VALUES (?, 0, 0, ?)
            """,
            (task_id, now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM em_monitor_tasks WHERE id = ?", (task_id,)).fetchone()
    return _public_task(row)


def update_monitor_task(task_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = get_monitor_task(task_id, user)
    match_mode = payload.get("matchMode", row["match_mode"])
    if match_mode not in ("simple", "regex"):
        raise ToolboxError("INVALID_MATCH_MODE", "匹配模式必须是 simple 或 regex", status_code=400, tool_id=TOOL_ID)
    alert_condition = payload.get("alertCondition", row["alert_condition"])
    if alert_condition not in ("below", "above", "changed"):
        raise ToolboxError("INVALID_ALERT_CONDITION", "报警条件类型无效", status_code=400, tool_id=TOOL_ID)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE em_monitor_tasks SET
                name = ?, description = ?, match_mode = ?, match_pattern = ?,
                filter_user = ?, alert_condition = ?, alert_threshold = ?,
                alert_change_amount = ?, confirm_count = ?,
                check_interval_seconds = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("name", row["name"]),
                payload.get("description", row["description"]),
                match_mode,
                payload.get("matchPattern", row["match_pattern"]),
                payload.get("filterUser", row["filter_user"]),
                alert_condition,
                int(payload.get("alertThreshold", row["alert_threshold"])),
                int(payload.get("alertChangeAmount", row["alert_change_amount"])),
                int(payload.get("confirmCount", row["confirm_count"])),
                int(payload.get("checkIntervalSeconds", row["check_interval_seconds"])),
                int(payload.get("enabled", row["enabled"])),
                now_iso(),
                task_id,
            ),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM em_monitor_tasks WHERE id = ?", (task_id,)).fetchone()
    return _public_task(updated)


def delete_monitor_task(task_id: str, user: User) -> None:
    get_monitor_task(task_id, user)
    with get_connection() as connection:
        connection.execute("UPDATE em_monitor_tasks SET enabled = 0, updated_at = ? WHERE id = ?", (now_iso(), task_id))
        connection.commit()


# ============================================================
# Alert Action Management
# ============================================================

def list_alert_actions(task_id: str, user: User) -> list[dict[str, Any]]:
    get_monitor_task(task_id, user)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM em_alert_actions WHERE task_id = ? AND enabled = 1 ORDER BY sort_order, created_at",
            (task_id,),
        ).fetchall()
    return [_public_action(row) for row in rows]


def create_alert_action(task_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    get_monitor_task(task_id, user)
    action_id = secrets.token_hex(12)
    action_type = payload.get("actionType", "email")
    if action_type not in ("email", "script"):
        raise ToolboxError("INVALID_ACTION_TYPE", "动作类型必须是 email 或 script", status_code=400, tool_id=TOOL_ID)

    now = now_iso()
    with get_connection() as connection:
        # Get max sort_order
        max_order = connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM em_alert_actions WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO em_alert_actions (
                id, task_id, action_type, email_recipients, email_subject_template, email_body_template,
                script_commands, script_screen_name, scripts_per_trigger, enabled, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                action_id,
                task_id,
                action_type,
                json.dumps(payload.get("emailRecipients", []), ensure_ascii=False),
                payload.get("emailSubjectTemplate", "实验监控报警: {task_name}"),
                payload.get("emailBodyTemplate", _default_email_body()),
                json.dumps(payload.get("scriptCommands", []), ensure_ascii=False),
                payload.get("scriptScreenName", ""),
                int(payload.get("scriptsPerTrigger", 1)),
                max_order + 1,
                now,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM em_alert_actions WHERE id = ?", (action_id,)).fetchone()
    return _public_action(row)


def update_alert_action(action_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM em_alert_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise ToolboxError("ACTION_NOT_FOUND", "报警动作不存在", status_code=404, tool_id=TOOL_ID)
    get_monitor_task(row["task_id"], user)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE em_alert_actions SET
                email_recipients = ?, email_subject_template = ?, email_body_template = ?,
                script_commands = ?, script_screen_name = ?, scripts_per_trigger = ?,
                enabled = ?, sort_order = ?
            WHERE id = ?
            """,
            (
                json.dumps(payload.get("emailRecipients", _json_list(row["email_recipients"])), ensure_ascii=False),
                payload.get("emailSubjectTemplate", row["email_subject_template"]),
                payload.get("emailBodyTemplate", row["email_body_template"]),
                json.dumps(payload.get("scriptCommands", _json_list(row["script_commands"])), ensure_ascii=False),
                payload.get("scriptScreenName", row["script_screen_name"]),
                int(payload.get("scriptsPerTrigger", row["scripts_per_trigger"])),
                int(payload.get("enabled", row["enabled"])),
                int(payload.get("sortOrder", row["sort_order"])),
                action_id,
            ),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM em_alert_actions WHERE id = ?", (action_id,)).fetchone()
    return _public_action(updated)


def delete_alert_action(action_id: str, user: User) -> None:
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM em_alert_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise ToolboxError("ACTION_NOT_FOUND", "报警动作不存在", status_code=404, tool_id=TOOL_ID)
    get_monitor_task(row["task_id"], user)
    with get_connection() as connection:
        connection.execute("UPDATE em_alert_actions SET enabled = 0 WHERE id = ?", (action_id,))
        connection.commit()


# ============================================================
# Email Configuration (Admin Only)
# ============================================================

def get_email_config(user: User) -> dict[str, Any]:
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以查看邮件配置", status_code=403, tool_id=TOOL_ID)
    init_database()
    config_keys = ["smtp_host", "smtp_port", "smtp_username", "smtp_password_encrypted", "smtp_from_address", "smtp_from_name"]
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT key, value FROM em_email_config WHERE key IN ({','.join(['?']*len(config_keys))})",
            config_keys,
        ).fetchall()
    config = {r["key"]: r["value"] for r in rows}
    return {
        "smtpHost": config.get("smtp_host", "smtp.buaa.edu.cn"),
        "smtpPort": int(config.get("smtp_port", "465")),
        "smtpUsername": config.get("smtp_username", ""),
        "smtpFromAddress": config.get("smtp_from_address", ""),
        "smtpFromName": config.get("smtp_from_name", "Experiment Monitor"),
        "configured": bool(config.get("smtp_host")),
    }


def save_email_config(payload: dict[str, Any], user: User) -> dict[str, Any]:
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以配置邮件设置", status_code=403, tool_id=TOOL_ID)
    init_database()
    now = now_iso()
    mappings = {
        "smtp_host": payload.get("smtpHost", "smtp.buaa.edu.cn"),
        "smtp_port": str(payload.get("smtpPort", 465)),
        "smtp_username": payload.get("smtpUsername", ""),
        "smtp_password_encrypted": encrypt_secret(payload.get("smtpPassword", "")) if payload.get("smtpPassword") else "",
        "smtp_from_address": payload.get("smtpFromAddress", ""),
        "smtp_from_name": payload.get("smtpFromName", "Experiment Monitor"),
    }
    with get_connection() as connection:
        for key, value in mappings.items():
            connection.execute(
                """
                INSERT INTO em_email_config (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        connection.commit()
    return get_email_config(user)


def send_email(
    recipients: list[str],
    subject: str,
    body: str,
) -> bool:
    """
    Send email using configured SMTP settings (SMTP_SSL mode).
    参照脚本使用 SSL 方式连接 SMTP 服务器（端口 465）。
    Returns True if successful, raises Exception on failure with detailed error message.
    """
    init_database()
    with get_connection() as connection:
        config_rows = connection.execute("SELECT key, value FROM em_email_config").fetchall()
    config = {r["key"]: r["value"] for r in config_rows}

    smtp_host = config.get("smtp_host", "")
    if not smtp_host:
        raise Exception("SMTP 服务器未配置")

    smtp_port = int(config.get("smtp_port", "465"))
    smtp_username = config.get("smtp_username", "")
    smtp_password = ""
    if config.get("smtp_password_encrypted"):
        try:
            smtp_password = decrypt_secret(config["smtp_password_encrypted"])
        except Exception:
            raise Exception("SMTP 密码解密失败")
    from_address = config.get("smtp_from_address", smtp_username)
    from_name = config.get("smtp_from_name", "Experiment Monitor")

    # 构建邮件（参照用户脚本的方式）
    msg = MIMEText(body, "plain", "utf-8")
    # ⚠️ 必须带邮箱，否则可能被判 fake sender
    msg["From"] = f"{from_name} <{from_address}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")

    try:
        # 使用 SMTP_SSL 方式连接（端口 465）
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        server.login(smtp_username, smtp_password)
        server.sendmail(from_address, recipients, msg.as_string())
        server.quit()
        logger.info("Email sent successfully to %s", recipients)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        raise Exception(f"SMTP 认证失败: {exc}") from exc
    except smtplib.SMTPConnectError as exc:
        raise Exception(f"无法连接到 SMTP 服务器 {smtp_host}:{smtp_port}: {exc}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise Exception(f"收件人被拒绝: {recipients}") from exc
    except smtplib.SMTPServerDisconnected as exc:
        raise Exception(f"SMTP 连接断开: {exc}") from exc
    except TimeoutError as exc:
        raise Exception(f"连接超时 ({smtp_host}:{smtp_port}): {exc}") from exc
    except Exception as exc:
        raise Exception(f"邮件发送失败: {type(exc).__name__}: {exc}") from exc


# ============================================================
# Process Monitoring & Alert Engine
# ============================================================

def check_process_count(task_row: sqlite3.Row, server_row: sqlite3.Row) -> tuple[int, list[str]]:
    """Execute SSH command to count matching processes. Returns (count, process_info_list)."""
    username_filter = task_row["filter_user"]
    pattern = task_row["match_pattern"]
    mode = task_row["match_mode"]

    # Build ps command to list processes
    if username_filter:
        ps_cmd = f"ps -u {shlex_quote(username_filter)} -o pid=,user=,args= 2>/dev/null"
    else:
        ps_cmd = "ps -eo pid=,user=,args= 2>/dev/null"

    # Full command: get processes and check screen availability
    full_cmd = f'{ps_cmd}; echo "---SCREEN_CHECK---"; which screen >/dev/null 2>&1 && echo "HAS_SCREEN" || echo "NO_SCREEN"'

    output = _run_ssh(server_row, full_cmd, timeout=15)

    # Parse output
    parts = output.split("---SCREEN_CHECK---")
    ps_output = parts[0] if parts else output
    has_screen = len(parts) > 1 and "HAS_SCREEN" in parts[1]

    matched_processes: list[str] = []
    for line in ps_output.splitlines():
        line = line.strip()
        if not line:
            continue
        if mode == "simple":
            if pattern.lower() in line.lower():
                matched_processes.append(line)
        elif mode == "regex":
            try:
                if re.search(pattern, line):
                    matched_processes.append(line)
            except re.error:
                # If regex is invalid, fall back to simple matching
                if pattern.lower() in line.lower():
                    matched_processes.append(line)

    return len(matched_processes), matched_processes, has_screen


def evaluate_condition(current_count: int, task_row: sqlite3.Row, alert_state: sqlite3.Row | None) -> tuple[bool, str]:
    """
    Evaluate whether the alert condition is met for this single sample.
    Returns (condition_met, reason).

    注意：多次确认逻辑在 run_monitor_check 中处理，这里只判断单次采样是否满足条件。
    对于 changed 模式，使用 alert_state 中的 last_check_count 与当前值比较，
    但以不处于连续报警中的上一次稳定值作为参考（防止进程短暂空隙的误判依赖在外层保障）。
    """
    condition = task_row["alert_condition"]

    if condition == "below":
        threshold = task_row["alert_threshold"]
        met = current_count < threshold
        reason = f"进程数 {current_count} < 阈值 {threshold}" if met else f"进程数 {current_count} >= 阈值 {threshold}"
    elif condition == "above":
        threshold = task_row["alert_threshold"]
        met = current_count > threshold
        reason = f"进程数 {current_count} > 阈值 {threshold}" if met else f"进程数 {current_count} <= 阈值 {threshold}"
    elif condition == "changed":
        change_amount = task_row["alert_change_amount"]
        # 使用上一次稳定计数（上次未在异常状态时记录的值）作为基准
        last_count = alert_state["last_check_count"] if alert_state and alert_state["last_check_count"] is not None else current_count
        diff = abs(current_count - last_count)
        met = diff >= change_amount
        direction = "增加" if current_count > last_count else "减少"
        reason = f"进程数{direction} {diff}（>= 变动阈值 {change_amount}）" if met else f"变动 {diff} < 阈值 {change_amount}"
    else:
        met = False
        reason = "未知条件类型"

    return met, reason


def run_monitor_check(task_id: str) -> dict[str, Any]:
    """Run a single monitoring check for a task. Called by scheduler.

    多次确认逻辑说明
    ─────────────────
    设计目的：防止进程在短暂重启间隙（一个进程刚结束、另一个还没起来）被采样到，
    导致"低于阈值"的误报警。

    规则：
    1. 每次采样判断当前是否满足报警条件（condition_met）。
    2. 若满足，consecutive_meets +1；若不满足，立刻清零（说明是瞬时抖动，进程已恢复）。
    3. 只有 consecutive_meets 达到 confirm_count 时才真正触发报警。
    4. 触发报警后：
       - 将当前稳定进程列表记录为 baseline_processes（供邮件展示"报警前的进程列表"）
       - 重置 consecutive_meets = 0，is_alerting = 0（允许下次独立触发，不做持续报警去重）
       - 对于 changed 模式：将当前 process_count 作为新的 last_check_count 基准，
         避免每次都与触发前的旧基准比较而持续触发。
    5. 正常情况下（未在报警中）每次更新 last_check_count 为当前值，
       并将 last_matched_processes 更新为当前进程列表（供邮件展示"报警时的进程变化"）。
    """
    init_database()

    # Fetch task and server separately to avoid JOIN column ambiguity
    with get_connection() as connection:
        task = connection.execute("SELECT * FROM em_monitor_tasks WHERE id = ? AND enabled = 1", (task_id,)).fetchone()

    if task is None:
        return {"status": "skipped", "reason": "任务不存在或已禁用"}

    with get_connection() as connection:
        server = connection.execute("SELECT * FROM em_servers WHERE id = ?", (task["server_id"],)).fetchone()
        state = connection.execute("SELECT * FROM em_alert_states WHERE task_id = ?", (task_id,)).fetchone()

    if server is None:
        return {"status": "skipped", "reason": "关联服务器不存在"}

    error = None
    process_count = 0
    matched_processes: list[str] = []
    has_screen = False
    condition_met = False
    condition_reason = ""

    try:
        process_count, matched_processes, has_screen = check_process_count(task, server)
        condition_met, condition_reason = evaluate_condition(process_count, task, state)
    except Exception as exc:
        error = str(exc)
        logger.exception("Monitor check failed for task %s: %s", task_id, exc)

    # 读取当前状态
    prev_consecutive = state["consecutive_meets"] if state else 0
    # 报警前的进程列表（上一次采样），供邮件展示"异常发生前的进程列表"
    prev_processes: list[str] = _json_list(state["last_matched_processes"]) if state else []
    # 当前记录的基准进程列表（触发后保留）
    baseline_processes: list[str] = _json_list(state["baseline_processes"]) if state else []

    # ── 多次确认逻辑 ──────────────────────────────────────────
    # 条件满足：计数累加；条件不满足（进程恢复）：立即清零，防止进程短暂空隙积累计数
    if condition_met:
        new_consecutive = prev_consecutive + 1
    else:
        new_consecutive = 0  # 只要有一次恢复正常，清零——说明之前是短暂抖动

    confirm_needed = task["confirm_count"]
    should_trigger = (condition_met and new_consecutive >= confirm_needed)

    # 触发时：保存报警前的进程列表作为 baseline，并重置计数
    if should_trigger:
        # baseline_processes = 触发前一刻的进程列表（即 prev_processes，来自上次采样）
        new_baseline_processes = prev_processes
        new_consecutive = 0  # 触发后重置，允许下次独立再触发
        # changed 模式：以当前值更新基准，避免持续触发
        new_last_check_count = process_count
    else:
        new_baseline_processes = baseline_processes
        # changed 模式：未触发时只在"无异常"状态下更新基准（防止连续异常中基准漂移）
        if not condition_met:
            new_last_check_count = process_count  # 正常状态下持续更新基准
        else:
            # 处于连续确认积累中，保持原有基准不变，等触发后再重置
            new_last_check_count = state["last_check_count"] if state and state["last_check_count"] is not None else process_count

    # Record sample
    sample_id = secrets.token_hex(12)
    now = now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO em_samples (id, task_id, checked_at, process_count, matched_processes, condition_met, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sample_id, task_id, now, process_count, json.dumps(matched_processes[:50], ensure_ascii=False), 1 if condition_met else 0, error),
        )

        # 更新报警状态
        connection.execute(
            """
            INSERT INTO em_alert_states (
                task_id, consecutive_meets, last_check_count,
                last_matched_processes, baseline_processes,
                is_alerting, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                consecutive_meets = excluded.consecutive_meets,
                last_check_count = excluded.last_check_count,
                last_matched_processes = excluded.last_matched_processes,
                baseline_processes = excluded.baseline_processes,
                is_alerting = 0,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                new_consecutive,
                new_last_check_count,
                json.dumps(matched_processes[:50], ensure_ascii=False),
                json.dumps(new_baseline_processes[:50], ensure_ascii=False),
                now,
            ),
        )
        connection.commit()

    # Trigger actions if needed
    if should_trigger:
        _trigger_alert_actions(task, server, process_count, condition_reason, matched_processes, new_baseline_processes, has_screen)

    # Cleanup old samples
    _prune_samples(task_id)

    return {
        "status": "ok",
        "taskId": task_id,
        "processCount": process_count,
        "conditionMet": condition_met,
        "conditionReason": condition_reason,
        "consecutiveMeets": new_consecutive,
        "shouldTrigger": should_trigger,
        "error": error,
    }


def _trigger_alert_actions(
    task: sqlite3.Row,
    server: sqlite3.Row,
    process_count: int,
    reason: str,
    matched_processes: list[str],
    baseline_processes: list[str],
    has_screen: bool,
) -> None:
    """Execute all enabled alert actions for a triggered alert."""
    task_id = task["id"]

    # Get server info for templates
    server_name = server["name"]
    task_name = task["name"]
    threshold_str = ""
    if task["alert_condition"] in ("below", "above"):
        op = "<" if task["alert_condition"] == "below" else ">"
        threshold_str = f"{op} {task['alert_threshold']}"
    else:
        threshold_str = f"变动 >= {task['alert_change_amount']}"

    with get_connection() as connection:
        actions = connection.execute(
            "SELECT * FROM em_alert_actions WHERE task_id = ? AND enabled = 1 ORDER BY sort_order, created_at",
            (task_id,),
        ).fetchall()

    for action in actions:
        action_type = action["action_type"]
        try:
            if action_type == "email":
                _execute_email_action(action, task_name, server_name, process_count, threshold_str, reason, matched_processes, baseline_processes)
            elif action_type == "script":
                _execute_script_action(action, server, has_screen)

            # Record event
            _record_event(task_id, action["id"], f"{action_type}_executed",
                         f"成功执行{('邮件通知' if action_type == 'email' else '脚本触发')}动作",
                         {"actionType": action_type})
        except Exception as exc:
            logger.exception("Failed to execute alert action %s: %s", action["id"], exc)
            _record_event(task_id, action["id"], f"{action_type}_failed",
                         f"执行{'邮件通知' if action_type == 'email' else '脚本触发'}失败: {exc}",
                         {"actionType": action_type, "error": str(exc)})

    # Record trigger event
    _record_event(task_id, None, "triggered",
                 f"报警已触发: {reason}",
                 {"processCount": process_count, "matchedProcesses": len(matched_processes)})


def _execute_email_action(
    action: sqlite3.Row,
    task_name: str,
    server_name: str,
    process_count: int,
    threshold_str: str,
    reason: str,
    matched_processes: list[str],
    baseline_processes: list[str],
) -> None:
    recipients = _json_list(action["email_recipients"])
    if not recipients:
        logger.warning("No recipients configured for email action %s", action["id"])
        return

    # 构建进程列表文本，供模板使用
    def _proc_list_text(procs: list[str], label: str) -> str:
        if not procs:
            return f"（{label}为空）"
        lines = [f"  {p}" for p in procs[:20]]
        if len(procs) > 20:
            lines.append(f"  ... 共 {len(procs)} 个进程")
        return "\n".join(lines)

    current_processes_text = _proc_list_text(matched_processes, "当前进程列表")
    prev_processes_text = _proc_list_text(baseline_processes, "报警前进程列表")

    template_vars = {
        "task_name": task_name,
        "server_name": server_name,
        "current_count": str(process_count),
        "prev_count": str(len(baseline_processes)),
        "threshold": threshold_str,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "reason": reason,
        # 当前采样到的进程名称列表（触发时）
        "current_processes": current_processes_text,
        # 报警触发前一刻记录的进程列表（基准）
        "prev_processes": prev_processes_text,
    }

    subject = action["email_subject_template"].format(**template_vars)
    body = action["email_body_template"].format(**template_vars)

    try:
        send_email(recipients, subject, body)
        _record_event(action["task_id"], action["id"], "email_sent",
                     f"邮件已发送给 {len(recipients)} 个收件人",
                     {"recipients": recipients})
    except Exception as exc:
        raise ToolboxError("EMAIL_SEND_FAILED", f"邮件发送失败: {exc}", status_code=500, tool_id=TOOL_ID) from exc


def _execute_script_action(action: sqlite3.Row, server: sqlite3.Row, has_screen: bool) -> None:
    commands = _json_list(action["script_commands"])
    if not commands:
        return

    scripts_per_trigger = action["scripts_per_trigger"] or 1
    screen_name_prefix = action["script_screen_name"] or f"em_trigger_{action['id'][:8]}"

    # Determine how many scripts to execute this time
    # Use a queue-like approach: track which scripts have been executed
    commands_to_run = commands[:scripts_per_trigger]

    for i, cmd in enumerate(commands_to_run):
        if has_screen and screen_name_prefix:
            screen_session = f"{screen_name_prefix}_{i}_{int(time.time())}"
            full_cmd = f'screen -dmS {shlex_quote(screen_session)} bash -c {shlex_quote(cmd + "; exec bash")}'
        else:
            full_cmd = cmd

        try:
            output = _run_ssh(server, full_cmd, timeout=30)
            _record_event(action["task_id"], action["id"], "script_executed",
                         f"脚本已在远程服务器执行{(f' (screen: {screen_session})' if has_screen else '')}",
                         {"command": cmd[:200], "screenSession": screen_session if has_screen else None,
                          "output": output[:500] if output else None})
        except Exception as exc:
            raise ToolboxError("SCRIPT_EXEC_FAILED", f"脚本执行失败: {exc}", status_code=500, tool_id=TOOL_ID) from exc


# ============================================================
# History & Events
# ============================================================

def get_task_history(task_id: str, user: User, hours: int = 24) -> dict[str, Any]:
    get_monitor_task(task_id, user)
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * RETENTION_DAYS)))
    with get_connection() as connection:
        samples = connection.execute(
            "SELECT * FROM em_samples WHERE task_id = ? AND checked_at >= ? ORDER BY checked_at ASC",
            (task_id, since.isoformat()),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM em_alert_events WHERE task_id = ? AND created_at >= ? ORDER BY created_at DESC",
            (task_id, since.isoformat()),
        ).fetchall()
        state = connection.execute("SELECT * FROM em_alert_states WHERE task_id = ?", (task_id,)).fetchone()
    return {
        "samples": [_public_sample(s) for s in samples],
        "events": [_public_event(e) for e in events],
        "alertState": _public_alert_state(state) if state else None,
    }


def get_alert_state(task_id: str, user: User) -> dict[str, Any] | None:
    get_monitor_task(task_id, user)
    with get_connection() as connection:
        state = connection.execute("SELECT * FROM em_alert_states WHERE task_id = ?", (task_id,)).fetchone()
    return _public_alert_state(state) if state else None


def reset_alert_state(task_id: str, user: User) -> dict[str, Any]:
    """Manually reset alert state (resolve an active alert)."""
    get_monitor_task(task_id, user)
    now = now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO em_alert_states (task_id, consecutive_meets, is_alerting, resolved_at, updated_at)
            VALUES (?, 0, 0, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                consecutive_meets = 0,
                is_alerting = 0,
                resolved_at = excluded.resolved_at,
                updated_at = excluded.updated_at
            """,
            (task_id, now, now),
        )
        connection.commit()
        _record_event(task_id, None, "resolved", "报警状态已被手动重置", {})
        state = connection.execute("SELECT * FROM em_alert_states WHERE task_id = ?", (task_id,)).fetchone()
    return _public_alert_state(state)


# ============================================================
# Scheduler entry point
# ============================================================

def collect_due_checks() -> None:
    """Called by scheduler to run all due monitoring checks."""
    init_database()
    with get_connection() as connection:
        tasks = connection.execute(
            "SELECT id FROM em_monitor_tasks WHERE enabled = 1",
        ).fetchall()

    for task_row in tasks:
        task_id = task_row["id"]
        if task_id:
            try:
                run_monitor_check(task_id)
            except Exception:
                logger.exception("Failed to run monitor check for task %s", task_id)


# ============================================================
# SSH Utilities
# ============================================================

def _run_ssh(row: sqlite3.Row | Any, command: str, timeout: int = 20) -> str:
    """Run a command via SSH. `row` can be a real Row or an object with dict-like access."""
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖，无法执行 SSH 命令", status_code=500, tool_id=TOOL_ID) from exc

    # Extract values from either sqlite3.Row or dict-like object
    def _get(key: str) -> str:
        if hasattr(row, 'keys'):
            return row[key]
        return getattr(row, key, '')

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=_get("host"),
            port=int(_get("port")),
            username=_get("ssh_username"),
            password=decrypt_secret(_get("ssh_password_encrypted")),
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace").strip()
        if error and not output:
            raise ToolboxError("SSH_COMMAND_FAILED", error[:300], status_code=502, tool_id=TOOL_ID)
        return output
    finally:
        client.close()


def shlex_quote(s: str) -> str:
    """Simple shell quoting for safe command construction."""
    import shlex
    return shlex.quote(s)


# ============================================================
# Encryption Utilities (shared with server_monitor pattern)
# ============================================================

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "无法解密敏感数据", status_code=400, tool_id=TOOL_ID) from exc


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().session_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


# ============================================================
# Internal helpers
# ============================================================

def _record_event(task_id: str, action_id: str | None, event_type: str, message: str, details: dict[str, Any] | None = None) -> None:
    init_database()
    event_id = secrets.token_hex(12)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO em_alert_events (id, task_id, action_id, event_type, message, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, task_id, action_id, event_type, message, json.dumps(details or {}, ensure_ascii=False), now_iso()),
        )
        connection.commit()


def _prune_samples(task_id: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    with get_connection() as connection:
        connection.execute("DELETE FROM em_samples WHERE task_id = ? AND checked_at < ?", (task_id, cutoff.isoformat()))
        connection.commit()


def _public_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "sshUsername": row["ssh_username"],
        "updatedAt": row["updated_at"],
    }


def _public_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "serverId": row["server_id"],
        "name": row["name"],
        "description": row["description"],
        "matchMode": row["match_mode"],
        "matchPattern": row["match_pattern"],
        "filterUser": row["filter_user"],
        "alertCondition": row["alert_condition"],
        "alertThreshold": row["alert_threshold"],
        "alertChangeAmount": row["alert_change_amount"],
        "confirmCount": row["confirm_count"],
        "checkIntervalSeconds": row["check_interval_seconds"],
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _public_action(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "actionType": row["action_type"],
        "emailRecipients": _json_list(row["email_recipients"]),
        "emailSubjectTemplate": row["email_subject_template"],
        "emailBodyTemplate": row["email_body_template"],
        "scriptCommands": _json_list(row["script_commands"]),
        "scriptScreenName": row["script_screen_name"],
        "scriptsPerTrigger": row["scripts_per_trigger"],
        "sortOrder": row["sort_order"],
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
    }


def _public_sample(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "checkedAt": row["checked_at"],
        "processCount": row["process_count"],
        "matchedProcesses": _json_list(row["matched_processes"]),
        "conditionMet": bool(row["condition_met"]),
        "error": row["error"],
    }


def _public_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "actionId": row["action_id"],
        "eventType": row["event_type"],
        "message": row["message"],
        "details": json.loads(row["details_json"]) if row["details_json"] else {},
        "createdAt": row["created_at"],
    }


def _public_alert_state(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "taskId": row["task_id"],
        "consecutiveMeets": row["consecutive_meets"],
        "lastCheckCount": row["last_check_count"],
        "isAlerting": bool(row["is_alerting"]),
        "lastAlertedAt": row["last_alerted_at"],
        "resolvedAt": row["resolved_at"],
        "updatedAt": row["updated_at"],
    }


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ToolboxError("INVALID_INPUT", f"字段 {key} 不能为空", status_code=400, tool_id=TOOL_ID)
    return value


def _default_email_body() -> str:
    return """实验监控报警通知

监控任务: {task_name}
服务器: {server_name}
触发原因: {reason}
触发时间: {time}

阈值条件: {threshold}
报警前进程数: {prev_count}
当前进程数: {current_count}

── 报警前的进程列表（基准）──
{prev_processes}

── 当前采样到的进程列表 ──
{current_processes}

此邮件由实验监控系统自动发送。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
