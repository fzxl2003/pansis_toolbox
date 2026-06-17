from __future__ import annotations

import base64
import hashlib
import logging
import smtplib
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database

logger = logging.getLogger(__name__)


# ============================================================
# Encryption Utilities
# ============================================================

def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().session_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "无法解密敏感数据", status_code=400) from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Email Configuration (Platform-level, Admin Only)
# ============================================================

def get_email_config() -> dict[str, Any]:
    """Get platform-level email configuration (without password)."""
    init_database()
    config_keys = ["smtp_host", "smtp_port", "smtp_username", "smtp_from_address", "smtp_from_name"]
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT key, value FROM platform_email_config WHERE key IN ({','.join(['?']*len(config_keys))})",
            config_keys,
        ).fetchall()
    config = {r["key"]: r["value"] for r in rows}
    return {
        "smtpHost": config.get("smtp_host", ""),
        "smtpPort": int(config.get("smtp_port", "465")),
        "smtpUsername": config.get("smtp_username", ""),
        "smtpFromAddress": config.get("smtp_from_address", ""),
        "smtpFromName": config.get("smtp_from_name", "实验监控系统"),
        "configured": bool(config.get("smtp_host")),
    }


def save_email_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Save platform-level email configuration. Password is encrypted at rest."""
    init_database()
    now = _now_iso()
    mappings: dict[str, str] = {
        "smtp_host": payload.get("smtpHost", ""),
        "smtp_port": str(payload.get("smtpPort", 465)),
        "smtp_username": payload.get("smtpUsername", ""),
        "smtp_from_address": payload.get("smtpFromAddress", ""),
        "smtp_from_name": payload.get("smtpFromName", "实验监控系统"),
    }
    # Only update password if a new one is provided
    if payload.get("smtpPassword"):
        mappings["smtp_password_encrypted"] = _encrypt_secret(payload["smtpPassword"])

    with get_connection() as connection:
        for key, value in mappings.items():
            connection.execute(
                """
                INSERT INTO platform_email_config (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        connection.commit()
    return get_email_config()


# ============================================================
# Email Sending
# ============================================================

def send_email(
    recipients: list[str],
    subject: str,
    body: str,
) -> bool:
    """
    Send email using platform-level SMTP configuration (SMTP_SSL mode, port 465).
    Returns True if successful, raises Exception on failure with detailed error message.
    """
    init_database()
    with get_connection() as connection:
        config_rows = connection.execute("SELECT key, value FROM platform_email_config").fetchall()
    config = {r["key"]: r["value"] for r in config_rows}

    smtp_host = config.get("smtp_host", "")
    if not smtp_host:
        raise Exception("平台邮件 SMTP 服务器未配置，请在「设置 → 邮件配置」中进行设置")

    smtp_port = int(config.get("smtp_port", "465"))
    smtp_username = config.get("smtp_username", "")
    smtp_password = ""
    if config.get("smtp_password_encrypted"):
        try:
            smtp_password = _decrypt_secret(config["smtp_password_encrypted"])
        except Exception:
            raise Exception("SMTP 密码解密失败，请重新配置邮件设置")

    from_address = config.get("smtp_from_address", smtp_username)
    from_name = config.get("smtp_from_name", "实验监控系统")

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = f"{from_name} <{from_address}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")

    try:
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
