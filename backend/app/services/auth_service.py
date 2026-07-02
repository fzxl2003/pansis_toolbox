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
    role: str = "user"
    disabled: bool = False

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "username": self.username,
            "displayName": self.display_name,
            "role": self.role,
            "disabled": self.disabled,
        }


def ensure_default_user() -> None:
    init_database()
    settings = get_settings()
    with get_connection() as connection:
        existing = connection.execute("SELECT id FROM users WHERE username = ?", (settings.default_admin_username,)).fetchone()
        if existing:
            connection.execute(
                "UPDATE users SET role = 'admin', disabled = 0 WHERE username = ?",
                (settings.default_admin_username,),
            )
            connection.commit()
            return
        salt = secrets.token_hex(16)
        connection.execute(
            """
            INSERT INTO users (id, username, display_name, password_hash, password_salt, role, disabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                secrets.token_hex(12),
                settings.default_admin_username,
                settings.default_admin_display_name,
                hash_password(settings.default_admin_password, salt),
                salt,
                "admin",
                0,
                now_iso(),
            ),
        )
        connection.commit()


def login(username: str, password: str) -> tuple[User, str]:
    ensure_default_user()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or row["disabled"] or not verify_password(password, row["password_salt"], row["password_hash"]):
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
        if row is None or row["disabled"]:
            return None
        return user_from_row(row)


def list_users() -> list[User]:
    ensure_default_user()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM users ORDER BY role = 'admin' DESC, created_at ASC"
        ).fetchall()
    return [user_from_row(row) for row in rows]


def create_user(username: str, display_name: str, password: str, role: str = "user") -> User:
    ensure_default_user()
    if role not in {"admin", "user"}:
        raise ToolboxError("INVALID_ROLE", "用户角色不合法", status_code=400)
    if not username.strip() or not password:
        raise ToolboxError("INVALID_USER", "用户名和密码不能为空", status_code=400)
    salt = secrets.token_hex(16)
    user_id = secrets.token_hex(12)
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO users (id, username, display_name, password_hash, password_salt, role, disabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    user_id,
                    username.strip(),
                    display_name.strip() or username.strip(),
                    hash_password(password, salt),
                    salt,
                    role,
                    now_iso(),
                ),
            )
            connection.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ToolboxError("USER_EXISTS", "用户名已存在", status_code=409) from exc
        raise
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_from_row(row)


def set_user_disabled(user_id: str, disabled: bool) -> User:
    ensure_default_user()
    with get_connection() as connection:
        target = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise ToolboxError("USER_NOT_FOUND", "用户不存在", status_code=404)
        if target["role"] == "admin" and disabled:
            raise ToolboxError("CANNOT_DISABLE_ADMIN", "不能禁用管理员账号", status_code=400)
        connection.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))
        connection.commit()
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_from_row(row)


def reset_user_password(user_id: str, password: str) -> User:
    ensure_default_user()
    if not password:
        raise ToolboxError("INVALID_PASSWORD", "密码不能为空", status_code=400)
    salt = secrets.token_hex(16)
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ToolboxError("USER_NOT_FOUND", "用户不存在", status_code=404)
        connection.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (hash_password(password, salt), salt, user_id),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_from_row(updated)


def change_user_password(user: User, current_password: str, new_password: str) -> User:
    ensure_default_user()
    if not current_password or not new_password:
        raise ToolboxError("INVALID_PASSWORD", "当前密码和新密码不能为空", status_code=400)
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user.id,)).fetchone()
        if row is None or row["disabled"]:
            raise ToolboxError("USER_NOT_FOUND", "用户不存在", status_code=404)
        if not verify_password(current_password, row["password_salt"], row["password_hash"]):
            raise ToolboxError("INVALID_CURRENT_PASSWORD", "当前密码错误", status_code=400)
        salt = secrets.token_hex(16)
        connection.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (hash_password(new_password, salt), salt, user.id),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))
        connection.commit()
        updated = connection.execute("SELECT * FROM users WHERE id = ?", (user.id,)).fetchone()
    return user_from_row(updated)


def delete_user(user_id: str) -> None:
    ensure_default_user()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ToolboxError("USER_NOT_FOUND", "用户不存在", status_code=404)
        if row["role"] == "admin":
            raise ToolboxError("CANNOT_DELETE_ADMIN", "不能删除管理员账号", status_code=400)
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


def hash_token(token: str) -> str:
    secret = get_settings().session_secret.encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def user_from_row(row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        disabled=bool(row["disabled"]),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
