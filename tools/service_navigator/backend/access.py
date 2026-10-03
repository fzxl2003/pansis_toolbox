"""Private-site access users, passwords, and visitor sessions.."""

from __future__ import annotations

from .database import *
from .sites import _owner_site


def get_access_settings(user: User) -> dict[str, Any]:
    site = _owner_site(user)
    users = {item.id: item for item in list_users() if not item.disabled}
    with conn() as database:
        grants = database.execute("SELECT * FROM service_navigator_access_users WHERE site_id=? ORDER BY granted_at", (site["id"],)).fetchall()
        passwords = database.execute("SELECT * FROM service_navigator_access_passwords WHERE site_id=? ORDER BY created_at", (site["id"],)).fetchall()
    return {"visibility": site["visibility"], "users": [{"userId": row["user_id"], "username": users[row["user_id"]].username, "displayName": users[row["user_id"]].display_name} for row in grants if row["user_id"] in users], "passwords": [_password_public(row) for row in passwords]}

def set_visibility(visibility: str, user: User) -> dict[str, Any]:
    if visibility not in {"public", "private"}:
        raise ToolboxError("INVALID_VISIBILITY", "可见性必须为 public 或 private", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        database.execute("UPDATE service_navigator_sites SET visibility=?,updated_at=? WHERE id=?", (visibility, now_iso(), site["id"]))
        database.commit()
    return get_access_settings(user)

def add_access_user(username: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    target = next((item for item in list_users() if not item.disabled and item.username == username.strip()), None)
    if not target:
        raise ToolboxError("USER_NOT_FOUND", "用户不存在或已被禁用", status_code=404, tool_id=TOOL_ID)
    if target.id == user.id:
        raise ToolboxError("OWNER_ALREADY_ALLOWED", "所有者已拥有访问权限", status_code=400, tool_id=TOOL_ID)
    with conn() as database:
        database.execute("INSERT INTO service_navigator_access_users(site_id,user_id,granted_at) VALUES(?,?,?) ON CONFLICT(site_id,user_id) DO NOTHING", (site["id"], target.id, now_iso()))
        database.commit()
    return get_access_settings(user)

def remove_access_user(user_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        database.execute("DELETE FROM service_navigator_access_users WHERE site_id=? AND user_id=?", (site["id"], user_id))
        database.commit()

def _password_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "label": row["label"], "enabled": bool(row["enabled"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

def add_password(label: str, password: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    label, password = label.strip()[:100], password[:256]
    if not label or not password:
        raise ToolboxError("INVALID_ACCESS_PASSWORD", "密码名称和密码不能为空", status_code=400, tool_id=TOOL_ID)
    salt = secrets.token_hex(16)
    with conn() as database:
        rows = database.execute("SELECT password_hash,password_salt FROM service_navigator_access_passwords WHERE site_id=?", (site["id"],)).fetchall()
        if any(verify_password(password, row["password_salt"], row["password_hash"]) for row in rows):
            raise ToolboxError("DUPLICATE_ACCESS_PASSWORD", "该访问密码已存在", status_code=409, tool_id=TOOL_ID)
        password_id, now = uuid4().hex, now_iso()
        database.execute("INSERT INTO service_navigator_access_passwords(id,site_id,label,password_hash,password_salt,enabled,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (password_id, site["id"], label, hash_password(password, salt), salt, now, now))
        database.commit()
        row = database.execute("SELECT * FROM service_navigator_access_passwords WHERE id=?", (password_id,)).fetchone()
    return _password_public(row)

def update_password(password_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_access_passwords WHERE id=? AND site_id=?", (password_id, site["id"])).fetchone()
        if not row:
            raise ToolboxError("ACCESS_PASSWORD_NOT_FOUND", "访问密码不存在", status_code=404, tool_id=TOOL_ID)
        label = str(payload.get("label", row["label"]) or "").strip()[:100]
        if not label:
            raise ToolboxError("INVALID_ACCESS_PASSWORD", "密码名称不能为空", status_code=400, tool_id=TOOL_ID)
        enabled = 1 if payload.get("enabled", bool(row["enabled"])) else 0
        replacement = str(payload.get("password") or "")[:256]
        password_hash, salt, revoke = row["password_hash"], row["password_salt"], not enabled
        if replacement:
            others = database.execute("SELECT password_hash,password_salt FROM service_navigator_access_passwords WHERE site_id=? AND id<>?", (site["id"], password_id)).fetchall()
            if any(verify_password(replacement, item["password_salt"], item["password_hash"]) for item in others):
                raise ToolboxError("DUPLICATE_ACCESS_PASSWORD", "该访问密码已存在", status_code=409, tool_id=TOOL_ID)
            salt, password_hash, revoke = secrets.token_hex(16), "", True
            password_hash = hash_password(replacement, salt)
        database.execute("UPDATE service_navigator_access_passwords SET label=?,password_hash=?,password_salt=?,enabled=?,updated_at=? WHERE id=?", (label, password_hash, salt, enabled, now_iso(), password_id))
        if revoke:
            database.execute("DELETE FROM service_navigator_access_sessions WHERE password_id=?", (password_id,))
        database.commit()
        changed = database.execute("SELECT * FROM service_navigator_access_passwords WHERE id=?", (password_id,)).fetchone()
    return _password_public(changed)

def delete_password(password_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        database.execute("DELETE FROM service_navigator_access_sessions WHERE password_id=?", (password_id,))
        changed = database.execute("DELETE FROM service_navigator_access_passwords WHERE id=? AND site_id=?", (password_id, site["id"])).rowcount
        database.commit()
    if not changed:
        raise ToolboxError("ACCESS_PASSWORD_NOT_FOUND", "访问密码不存在", status_code=404, tool_id=TOOL_ID)

def public_site(slug: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_sites WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    owner = next((item for item in list_users() if item.id == row["owner_user_id"] and not item.disabled), None)
    return _row(row) if owner and can_access_tool(TOOL_ID, owner) else None

def site_access(site: dict[str, Any], user: User | None, visitor_token: str = "") -> dict[str, Any]:
    if user and user.id == site["owner_user_id"]:
        return {"allowed": True, "kind": "owner", "label": user.username}
    if user:
        with conn() as database:
            grant = database.execute("SELECT 1 FROM service_navigator_access_users WHERE site_id=? AND user_id=?", (site["id"], user.id)).fetchone()
        if grant:
            return {"allowed": True, "kind": "user", "label": user.username}
    visitor_hash = hash_token(visitor_token) if visitor_token else ""
    if visitor_hash:
        with conn() as database:
            grant = database.execute("""SELECT p.label FROM service_navigator_access_sessions s JOIN service_navigator_access_passwords p ON p.id=s.password_id WHERE s.site_id=? AND s.visitor_hash=? AND s.expires_at>? AND p.enabled=1""", (site["id"], visitor_hash, now_iso())).fetchone()
        if grant:
            return {"allowed": True, "kind": "password", "label": grant["label"]}
    return {"allowed": site["visibility"] == "public", "kind": "anonymous", "label": "匿名访客"}

def unlock_site(site: dict[str, Any], password: str, visitor_token: str) -> None:
    if not password or not visitor_token:
        raise ToolboxError("INVALID_ACCESS_PASSWORD", "访问密码错误", status_code=401, tool_id=TOOL_ID)
    with conn() as database:
        rows = database.execute("SELECT * FROM service_navigator_access_passwords WHERE site_id=? AND enabled=1", (site["id"],)).fetchall()
        match = next((row for row in rows if verify_password(password, row["password_salt"], row["password_hash"])), None)
        if not match:
            raise ToolboxError("INVALID_ACCESS_PASSWORD", "访问密码错误", status_code=401, tool_id=TOOL_ID)
        database.execute("INSERT INTO service_navigator_access_sessions(site_id,visitor_hash,password_id,expires_at,created_at) VALUES(?,?,?,?,?) ON CONFLICT(site_id,visitor_hash) DO UPDATE SET password_id=excluded.password_id,expires_at=excluded.expires_at,created_at=excluded.created_at", (site["id"], hash_token(visitor_token), match["id"], (datetime.now(timezone.utc) + timedelta(days=ACCESS_DAYS)).isoformat(), now_iso()))
        database.commit()

def lock_site(site: dict[str, Any], visitor_token: str) -> None:
    if not visitor_token:
        return
    with conn() as database:
        database.execute("DELETE FROM service_navigator_access_sessions WHERE site_id=? AND visitor_hash=?", (site["id"], hash_token(visitor_token)))
        database.commit()

__all__ = ['_password_public', 'add_access_user', 'add_password', 'delete_password', 'get_access_settings', 'lock_site', 'public_site', 'remove_access_user', 'set_visibility', 'site_access', 'unlock_site', 'update_password']
