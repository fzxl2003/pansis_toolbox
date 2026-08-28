"""Platform-owned SSH server configurations and access control.

Credentials are encrypted at rest and are deliberately never returned by the
public serializers.  A server belongs either to its creator (private), or to
an administrator as a shared configuration with an explicit audience.
"""
from __future__ import annotations

import base64
import hashlib
import io
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database
from backend.app.services.auth_service import User


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().session_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "无法解密 SSH 凭据", status_code=400) from exc


def _require_server_admin(user: User) -> None:
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "SSH 服务器配置仅管理员可管理，普通用户只能使用已授权的服务器", status_code=403)


def _normalise(payload: dict[str, Any], *, creating: bool, current_auth_type: str | None = None) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    host = str(payload.get("host") or "").strip()
    username = str(payload.get("sshUsername") or "").strip()
    try:
        port = int(payload.get("port") or 22)
    except (TypeError, ValueError) as exc:
        raise ToolboxError("INVALID_PORT", "SSH 端口必须是整数", status_code=400) from exc
    if not name or not host or not username:
        raise ToolboxError("INVALID_INPUT", "服务器名称、地址和 SSH 用户名不能为空", status_code=400)
    if not 1 <= port <= 65535:
        raise ToolboxError("INVALID_PORT", "SSH 端口必须在 1 到 65535 之间", status_code=400)
    auth_type = str(payload.get("authType") or current_auth_type or "password")
    if auth_type not in {"password", "private_key"}:
        raise ToolboxError("INVALID_AUTH_TYPE", "认证方式只能为密码或私钥", status_code=400)
    if creating and auth_type == "password" and not payload.get("sshPassword"):
        raise ToolboxError("PASSWORD_REQUIRED", "密码登录需要填写 SSH 密码", status_code=400)
    if creating and auth_type == "private_key" and not payload.get("privateKey"):
        raise ToolboxError("PRIVATE_KEY_REQUIRED", "私钥登录需要填写私钥内容", status_code=400)
    return {"name": name, "host": host, "port": port, "sshUsername": username, "authType": auth_type}


def _public(row: Any, *, allowed_user_ids: list[str] | None = None, can_manage: bool = False) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
        "sshUsername": row["ssh_username"], "authType": row["auth_type"],
        "isPublic": bool(row["is_public"]), "enabled": bool(row["enabled"]),
        "ownerUserId": row["owner_user_id"], "allowedUserIds": allowed_user_ids or [],
        "canManage": can_manage,
    }


def _allowed_ids(connection: Any, server_id: str) -> list[str]:
    return [r["user_id"] for r in connection.execute(
        "SELECT user_id FROM platform_ssh_server_user_access WHERE server_id=? ORDER BY user_id", (server_id,)
    ).fetchall()]


def _can_use_row(connection: Any, row: Any, user: User) -> bool:
    if user.role == "admin" or row["owner_user_id"] == user.id:
        return True
    return bool(row["is_public"]) and connection.execute(
        "SELECT 1 FROM platform_ssh_server_user_access WHERE server_id=? AND user_id=?", (row["id"], user.id)
    ).fetchone() is not None


def list_servers(user: User, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    init_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM platform_ssh_servers WHERE enabled=1 OR ? ORDER BY is_public DESC, name COLLATE NOCASE",
            (int(include_disabled and user.role == "admin"),),
        ).fetchall()
        result = []
        for row in rows:
            if _can_use_row(connection, row, user):
                manage = user.role == "admin"
                result.append(_public(row, allowed_user_ids=_allowed_ids(connection, row["id"]) if manage else [], can_manage=manage))
        return result


def get_server(server_id: str, user: User, *, require_manage: bool = False) -> dict[str, Any]:
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if row is None or not row["enabled"]:
            raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或已停用", status_code=404)
        manage = user.role == "admin"
        if (require_manage and not manage) or (not require_manage and not _can_use_row(connection, row, user)):
            raise ToolboxError("SERVER_ACCESS_DENIED", "没有该 SSH 服务器的使用权限", status_code=403)
        return _public(row, allowed_user_ids=_allowed_ids(connection, server_id) if manage else [], can_manage=manage)


def get_server_credentials(server_id: str, user: User) -> dict[str, Any]:
    """Internal tool adapter.  Authorizes first, then returns decrypted data."""
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if row is None or not row["enabled"] or not _can_use_row(connection, row, user):
            raise ToolboxError("SERVER_ACCESS_DENIED", "没有该 SSH 服务器的使用权限", status_code=403)
        return {
            "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
            "ssh_username": row["ssh_username"], "auth_type": row["auth_type"],
            "ssh_password": decrypt_secret(row["ssh_password_encrypted"]),
            "private_key": decrypt_secret(row["private_key_encrypted"]),
            "private_key_passphrase": decrypt_secret(row["private_key_passphrase_encrypted"]),
        }


def get_legacy_connection_row(server_id: str, user: User) -> dict[str, Any]:
    """Temporary adapter for tools while their non-SSH schemas are migrated.

    It is intentionally read-only: the platform table above remains the sole
    configuration authority.  The encrypted field names keep old command
    executors working without ever exposing a secret through an HTTP response.
    """
    values = get_server_credentials(server_id, user)
    return {
        **values,
        "ssh_password_encrypted": encrypt_secret(values["ssh_password"]),
        "private_key_encrypted": encrypt_secret(values["private_key"]),
        "private_key_passphrase_encrypted": encrypt_secret(values["private_key_passphrase"]),
        "enabled": 1,
        "owner_user_id": user.id,
        "has_screen": 0,
        "last_test_status": "unknown",
        "last_test_error": "",
        "last_tested_at": None,
        "created_at": "",
        "updated_at": "",
    }


def merge_connection_credentials(local_row: Any, user: User) -> dict[str, Any]:
    """Overlay a tool's non-secret binding row with its global SSH details."""
    merged = dict(local_row)
    credentials = get_legacy_connection_row(str(merged["id"]), user)
    merged.update(credentials)
    return merged


def load_private_key(key_text: str, passphrase: str | None, paramiko: Any) -> Any:
    """Parse the SSH key formats supported by Paramiko."""
    errors: list[str] = []
    for key_type in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
        key_class = getattr(paramiko, key_type, None)
        if key_class is None:
            continue
        try:
            return key_class.from_private_key(io.StringIO(key_text), password=passphrase or None)
        except Exception as exc:
            errors.append(f"{key_type}: {exc}")
    raise ToolboxError("PRIVATE_KEY_INVALID", "私钥无法解析或私钥口令不正确", status_code=400, extra={"details": errors[-2:]})


def migrate_legacy_server(
    *,
    server_id: str,
    owner: User,
    name: str,
    host: str,
    port: int,
    ssh_username: str,
    ssh_password: str = "",
    private_key: str = "",
    private_key_passphrase: str = "",
    auth_type: str = "password",
    source_tool: str,
    is_public: bool = False,
    allowed_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    """One-time import for an existing tool-local credential.

    The original id is deliberately retained so all existing task/session
    foreign keys continue to work.  Imports are private to the old owner and
    therefore do not grant any new access to other users.
    """
    init_database()
    with get_connection() as connection:
        existing = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if existing is not None:
            if existing["owner_user_id"] != owner.id and not is_public:
                raise ToolboxError("SSH_SERVER_ID_COLLISION", f"旧服务器 ID 与现有全局服务器冲突: {server_id}", status_code=409)
            return _public(existing, can_manage=owner.role == "admin")
        candidate = (name or source_tool).strip() or source_tool
        used = {r["name"].casefold() for r in connection.execute("SELECT name FROM platform_ssh_servers").fetchall()}
        if candidate.casefold() in used:
            base = f"{candidate} ({source_tool})"
            candidate, index = base, 2
            while candidate.casefold() in used:
                candidate = f"{base} {index}"
                index += 1
        normalized_auth = auth_type if auth_type in {"password", "private_key"} else "password"
        if normalized_auth == "private_key" and not private_key:
            normalized_auth = "password"
        now = _now()
        connection.execute(
            """INSERT INTO platform_ssh_servers
               (id,name,host,port,ssh_username,auth_type,ssh_password_encrypted,private_key_encrypted,private_key_passphrase_encrypted,owner_user_id,is_public,enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (server_id, candidate, host, int(port or 22), ssh_username, normalized_auth,
             encrypt_secret(ssh_password), encrypt_secret(private_key), encrypt_secret(private_key_passphrase),
             owner.id, int(is_public), 1, now, now),
        )
        if is_public:
            _replace_access(connection, server_id, list(dict.fromkeys(allowed_user_ids or [])), now)
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        return _public(row, can_manage=owner.role == "admin")


def create_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    _require_server_admin(user)
    values = _normalise(payload, creating=True)
    is_public = bool(payload.get("isPublic", False))
    now = _now(); server_id = str(uuid.uuid4())
    allowed = list(dict.fromkeys(payload.get("allowedUserIds") or [])) if is_public else []
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO platform_ssh_servers
               (id,name,host,port,ssh_username,auth_type,ssh_password_encrypted,private_key_encrypted,private_key_passphrase_encrypted,owner_user_id,is_public,enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (server_id, values["name"], values["host"], values["port"], values["sshUsername"], values["authType"],
             encrypt_secret(str(payload.get("sshPassword") or "")), encrypt_secret(str(payload.get("privateKey") or "")),
             encrypt_secret(str(payload.get("privateKeyPassphrase") or "")), user.id, int(is_public), 1, now, now),
        )
        _replace_access(connection, server_id, allowed, now)
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        return _public(row, allowed_user_ids=allowed, can_manage=True)


def _replace_access(connection: Any, server_id: str, user_ids: list[str], now: str) -> None:
    connection.execute("DELETE FROM platform_ssh_server_user_access WHERE server_id=?", (server_id,))
    for user_id in user_ids:
        if connection.execute("SELECT 1 FROM users WHERE id=? AND disabled=0", (user_id,)).fetchone():
            connection.execute("INSERT INTO platform_ssh_server_user_access (server_id,user_id,granted_at) VALUES (?,?,?)", (server_id, user_id, now))


def update_server(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在", status_code=404)
        _require_server_admin(user)
        merged = {"name": payload.get("name", row["name"]), "host": payload.get("host", row["host"]),
                  "port": payload.get("port", row["port"]), "sshUsername": payload.get("sshUsername", row["ssh_username"]),
                  "authType": payload.get("authType", row["auth_type"])}
        values = _normalise(merged, creating=False, current_auth_type=row["auth_type"])
        if values["authType"] == "password" and values["authType"] != row["auth_type"] and not payload.get("sshPassword"):
            raise ToolboxError("PASSWORD_REQUIRED", "切换到密码认证时需要填写 SSH 密码", status_code=400)
        if values["authType"] == "private_key" and values["authType"] != row["auth_type"] and not payload.get("privateKey"):
            raise ToolboxError("PRIVATE_KEY_REQUIRED", "切换到私钥认证时需要填写私钥内容", status_code=400)
        is_public = bool(payload.get("isPublic", row["is_public"]))
        now = _now()
        password = row["ssh_password_encrypted"] if not payload.get("sshPassword") else encrypt_secret(str(payload["sshPassword"]))
        private_key = row["private_key_encrypted"] if not payload.get("privateKey") else encrypt_secret(str(payload["privateKey"]))
        passphrase = row["private_key_passphrase_encrypted"] if "privateKeyPassphrase" not in payload else encrypt_secret(str(payload["privateKeyPassphrase"] or ""))
        connection.execute("""UPDATE platform_ssh_servers SET name=?,host=?,port=?,ssh_username=?,auth_type=?,ssh_password_encrypted=?,private_key_encrypted=?,private_key_passphrase_encrypted=?,is_public=?,updated_at=? WHERE id=?""",
            (values["name"], values["host"], values["port"], values["sshUsername"], values["authType"], password, private_key, passphrase, int(is_public), now, server_id))
        allowed = list(dict.fromkeys(payload.get("allowedUserIds", _allowed_ids(connection, server_id)))) if is_public else []
        _replace_access(connection, server_id, allowed, now)
        changed = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        return _public(changed, allowed_user_ids=allowed, can_manage=True)


def delete_server(server_id: str, user: User) -> None:
    init_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            return
        _require_server_admin(user)
        connection.execute("DELETE FROM platform_ssh_servers WHERE id=?", (server_id,))


def copy_server(server_id: str, name: str | None, user: User) -> dict[str, Any]:
    """Admin-only duplicate that keeps the copy in the global configuration."""
    init_database()
    _require_server_admin(user)
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在", status_code=404)
        now, new_id = _now(), str(uuid.uuid4())
        new_name = (name or f"{row['name']} (副本)").strip()
        if not new_name:
            raise ToolboxError("INVALID_INPUT", "服务器名称不能为空", status_code=400)
        connection.execute(
            """INSERT INTO platform_ssh_servers
               (id,name,host,port,ssh_username,auth_type,ssh_password_encrypted,private_key_encrypted,private_key_passphrase_encrypted,owner_user_id,is_public,enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id, new_name, row["host"], row["port"], row["ssh_username"], row["auth_type"],
             row["ssh_password_encrypted"], row["private_key_encrypted"], row["private_key_passphrase_encrypted"],
             user.id, row["is_public"], 1, now, now),
        )
        allowed = _allowed_ids(connection, server_id) if row["is_public"] else []
        _replace_access(connection, new_id, allowed, now)
        copied = connection.execute("SELECT * FROM platform_ssh_servers WHERE id=?", (new_id,)).fetchone()
        return _public(copied, allowed_user_ids=allowed, can_manage=True)


def test_connection(server_id: str, user: User) -> dict[str, Any]:
    credentials = get_server_credentials(server_id, user)
    try:
        import paramiko
        client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {"hostname": credentials["host"], "port": credentials["port"], "username": credentials["ssh_username"], "timeout": 15}
        if credentials["auth_type"] == "password":
            kwargs["password"] = credentials["ssh_password"]
        else:
            key_errors: list[str] = []
            key = None
            for key_type in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
                key_class = getattr(paramiko, key_type, None)
                if key_class is None:
                    continue
                try:
                    key = key_class.from_private_key(
                        io.StringIO(credentials["private_key"]),
                        password=credentials["private_key_passphrase"] or None,
                    )
                    break
                except Exception as exc:  # paramiko reports parser details per format
                    key_errors.append(f"{key_type}: {exc}")
            if key is None:
                raise ToolboxError("PRIVATE_KEY_INVALID", "私钥无法解析或私钥口令不正确", status_code=400, extra={"details": key_errors[-2:]})
            kwargs["pkey"] = key
        client.connect(**kwargs); _, stdout, stderr = client.exec_command("echo connected", timeout=10)
        stderr.read(); client.close()
        return {"connected": True, "message": "SSH 连接成功"}
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500) from exc
    except Exception as exc:
        raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 连接失败: {exc}", status_code=502) from exc
