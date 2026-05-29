from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database


SESSION_DAYS = 14


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "username": self.username, "displayName": self.display_name}


def ensure_default_user() -> None:
    init_database()
    settings = get_settings()
    with get_connection() as connection:
        existing = connection.execute("SELECT id FROM users WHERE username = ?", (settings.default_admin_username,)).fetchone()
        if existing:
            return
        salt = secrets.token_hex(16)
        connection.execute(
            """
            INSERT INTO users (id, username, display_name, password_hash, password_salt, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                secrets.token_hex(12),
                settings.default_admin_username,
                settings.default_admin_display_name,
                hash_password(settings.default_admin_password, salt),
                salt,
                now_iso(),
            ),
        )
        connection.commit()


def login(username: str, password: str) -> tuple[User, str]:
    ensure_default_user()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(password, row["password_salt"], row["password_hash"]):
            raise ToolboxError("INVALID_CREDENTIALS", "用户名或密码错误", status_code=401)

        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        connection.execute(
            """
            INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (secrets.token_hex(12), row["id"], token_hash, now_iso(), expires_at.isoformat()),
        )
        connection.commit()
        return user_from_row(row), token


def logout(token: str | None) -> None:
    if not token:
        return
    with get_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
        connection.commit()


def get_user_by_session_token(token: str | None) -> User | None:
    if not token:
        return None
    ensure_default_user()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (hash_token(token), now_iso()),
        ).fetchone()
        if row is None:
            return None
        return user_from_row(row)


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


def hash_token(token: str) -> str:
    secret = get_settings().session_secret.encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def user_from_row(row) -> User:
    return User(id=row["id"], username=row["username"], display_name=row["display_name"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
