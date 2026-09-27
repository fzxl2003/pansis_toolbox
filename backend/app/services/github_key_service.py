"""User-owned GitHub deploy keys.  Administrators have no bypass access."""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database
from backend.app.services.auth_service import User


def _now() -> str: return datetime.now(timezone.utc).isoformat()

def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(get_settings().session_secret.encode()).digest()))

def _public(row: Any) -> dict[str, str]:
    return {"id": row["id"], "name": row["name"], "publicKey": row["public_key"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

def list_keys(user: User) -> list[dict[str, str]]:
    init_database()
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM platform_github_keys WHERE owner_user_id=? ORDER BY created_at DESC", (user.id,)).fetchall()
    return [_public(row) for row in rows]

def create_key(name: str, user: User) -> dict[str, str]:
    init_database(); name = name.strip()
    if not name: raise ToolboxError("INVALID_GITHUB_KEY", "密钥名称不能为空", status_code=400)
    private = Ed25519PrivateKey.generate()
    # OpenSSH itself does not reliably accept PKCS#8-encoded Ed25519 keys.
    # Persist its native representation so the generated private key can be
    # passed directly to ``ssh -i`` during a Git deploy-key operation.
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    public = private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    now, key_id = _now(), uuid.uuid4().hex
    try:
        with get_connection() as conn:
            conn.execute("INSERT INTO platform_github_keys(id,owner_user_id,name,private_key_encrypted,public_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (key_id,user.id,name,_fernet().encrypt(private_pem.encode()).decode(),public,now,now))
            row = conn.execute("SELECT * FROM platform_github_keys WHERE id=?", (key_id,)).fetchone()
    except Exception as exc:
        if "UNIQUE" in str(exc).upper(): raise ToolboxError("GITHUB_KEY_EXISTS", "同名 GitHub 密钥已存在", status_code=409) from exc
        raise
    return _public(row)

def get_private_key(key_id: str, user: User) -> str:
    init_database()
    with get_connection() as conn:
        row = conn.execute("SELECT private_key_encrypted FROM platform_github_keys WHERE id=? AND owner_user_id=?", (key_id,user.id)).fetchone()
    if row is None: raise ToolboxError("GITHUB_KEY_NOT_FOUND", "GitHub 密钥不存在", status_code=404)
    try:
        private_key = _fernet().decrypt(row["private_key_encrypted"].encode()).decode()
    except InvalidToken as exc: raise ToolboxError("INVALID_SECRET", "无法解密 GitHub 密钥", status_code=400) from exc
    if private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
        return private_key
    # Keys created by the first release used PKCS#8.  Convert them on demand
    # and save the compatible form so existing users do not need to recreate
    # or re-register their GitHub Deploy Keys.
    try:
        key = serialization.load_pem_private_key(private_key.encode(), password=None)
        compatible_key = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
    except (TypeError, ValueError) as exc:
        raise ToolboxError("INVALID_GITHUB_KEY", "GitHub 私钥格式无效", status_code=400) from exc
    with get_connection() as conn:
        conn.execute(
            "UPDATE platform_github_keys SET private_key_encrypted=?, updated_at=? WHERE id=? AND owner_user_id=?",
            (_fernet().encrypt(compatible_key.encode()).decode(), _now(), key_id, user.id),
        )
    return compatible_key

def delete_key(key_id: str, user: User) -> None:
    init_database()
    with get_connection() as conn:
        result = conn.execute("DELETE FROM platform_github_keys WHERE id=? AND owner_user_id=?", (key_id,user.id))
    if result.rowcount == 0: raise ToolboxError("GITHUB_KEY_NOT_FOUND", "GitHub 密钥不存在", status_code=404)
