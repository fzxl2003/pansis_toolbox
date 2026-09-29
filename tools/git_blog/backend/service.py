from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import yaml
from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User, hash_password, hash_token, list_users, verify_password
from backend.app.services import github_key_service
from backend.app.services import proxy_service
from backend.app.services.tool_access_service import can_access_tool

TOOL_ID = "git_blog"
MAX_BLOGS_PER_USER = 10
MIN_SYNC_MINUTES, MAX_SYNC_MINUTES = 5, 1440
MAX_FILES, MAX_MARKDOWN_BYTES, MAX_ASSET_BYTES = 10_000, 2 * 1024 * 1024, 25 * 1024 * 1024
MAX_TEMPLATE_FILES, MAX_TEMPLATE_BYTES = 200, 10 * 1024 * 1024
MAX_THEME_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOTS = 5
BLOG_ACCESS_DAYS = 30
VISITOR_DAYS = 365
RENDER_PIPELINE_VERSION = "1"
SYNC_TIMEOUT = 300
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
EXCLUDED_DIRS = {".git", "node_modules", "dist", "build", ".next", "vendor", "__pycache__"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    path = get_settings().storage_dir / "data" / "tools" / TOOL_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_path() -> Path:
    return _root() / "data.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS git_blog_blogs (
        id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE, repo_url TEXT NOT NULL, branch TEXT NOT NULL,
        content_root TEXT NOT NULL DEFAULT '', sync_interval_minutes INTEGER NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}', effective_config_json TEXT NOT NULL DEFAULT '{}', token_encrypted TEXT NOT NULL DEFAULT '', github_key_id TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1, auto_sync_enabled INTEGER NOT NULL DEFAULT 1,
        visibility TEXT NOT NULL DEFAULT 'public', share_enabled INTEGER NOT NULL DEFAULT 0,
        everyone_can_share INTEGER NOT NULL DEFAULT 1,
        current_commit TEXT NOT NULL DEFAULT '',
        render_fingerprint TEXT NOT NULL DEFAULT '',
        sync_status TEXT NOT NULL DEFAULT 'pending', last_error TEXT NOT NULL DEFAULT '',
        last_success_at TEXT, last_attempt_at TEXT, next_sync_at TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS git_blog_articles (
        blog_id TEXT NOT NULL, slug TEXT NOT NULL, source_path TEXT NOT NULL,
        title TEXT NOT NULL, summary TEXT NOT NULL, author TEXT NOT NULL,
        published_at TEXT NOT NULL, updated_at TEXT NOT NULL, tags_json TEXT NOT NULL,
        cover_path TEXT NOT NULL DEFAULT '', html TEXT NOT NULL, plain_text TEXT NOT NULL,
        PRIMARY KEY(blog_id, slug), UNIQUE(blog_id, source_path)
      );
      CREATE TABLE IF NOT EXISTS git_blog_assets (
        blog_id TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(blog_id, path)
      );
      CREATE TABLE IF NOT EXISTS git_blog_runs (
        id TEXT PRIMARY KEY, blog_id TEXT NOT NULL, status TEXT NOT NULL, commit_hash TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '', warnings_json TEXT NOT NULL DEFAULT '[]', started_at TEXT NOT NULL, finished_at TEXT
      );
      CREATE TABLE IF NOT EXISTS git_blog_snapshots (
        blog_id TEXT NOT NULL, commit_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(blog_id, commit_hash)
      );
      CREATE TABLE IF NOT EXISTS git_blog_themes (
        id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, name TEXT NOT NULL,
        filename TEXT NOT NULL, css BLOB NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(owner_user_id, name)
      );
      CREATE TABLE IF NOT EXISTS git_blog_access_logs (
        id TEXT PRIMARY KEY, blog_id TEXT NOT NULL, requested_at TEXT NOT NULL,
        path TEXT NOT NULL, method TEXT NOT NULL, status_code INTEGER NOT NULL,
        article_slug TEXT NOT NULL DEFAULT '', remote_address TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS git_blog_access_users (
        blog_id TEXT NOT NULL, user_id TEXT NOT NULL, can_share INTEGER NOT NULL DEFAULT 0,
        granted_at TEXT NOT NULL, PRIMARY KEY(blog_id, user_id)
      );
      CREATE TABLE IF NOT EXISTS git_blog_access_passwords (
        id TEXT PRIMARY KEY, blog_id TEXT NOT NULL, label TEXT NOT NULL,
        password_hash TEXT NOT NULL, password_salt TEXT NOT NULL,
        can_share INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS git_blog_access_sessions (
        blog_id TEXT NOT NULL, visitor_hash TEXT NOT NULL, password_id TEXT NOT NULL,
        expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(blog_id, visitor_hash)
      );
      CREATE TABLE IF NOT EXISTS git_blog_shares (
        id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, blog_id TEXT NOT NULL,
        article_slug TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'document',
        password_hash TEXT NOT NULL DEFAULT '', password_salt TEXT NOT NULL DEFAULT '',
        password_version INTEGER NOT NULL DEFAULT 0, expires_at TEXT, max_views INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1, created_by_type TEXT NOT NULL,
        created_by_label TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS git_blog_share_visitors (
        share_id TEXT NOT NULL, visitor_hash TEXT NOT NULL,
        first_opened_at TEXT NOT NULL, last_opened_at TEXT NOT NULL,
        PRIMARY KEY(share_id, visitor_hash)
      );
      CREATE TABLE IF NOT EXISTS git_blog_share_unlocks (
        share_id TEXT NOT NULL, visitor_hash TEXT NOT NULL, password_version INTEGER NOT NULL,
        expires_at TEXT NOT NULL, PRIMARY KEY(share_id, visitor_hash)
      );
      CREATE INDEX IF NOT EXISTS idx_git_blog_articles_date ON git_blog_articles(blog_id, published_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_runs_blog ON git_blog_runs(blog_id, started_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_snapshots_blog ON git_blog_snapshots(blog_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_themes_owner ON git_blog_themes(owner_user_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_access_logs_blog ON git_blog_access_logs(blog_id, requested_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_access_users_blog ON git_blog_access_users(blog_id);
      CREATE INDEX IF NOT EXISTS idx_git_blog_access_passwords_blog ON git_blog_access_passwords(blog_id);
      CREATE INDEX IF NOT EXISTS idx_git_blog_shares_blog ON git_blog_shares(blog_id, created_at DESC);
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(git_blog_blogs)").fetchall()}
    if "effective_config_json" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN effective_config_json TEXT NOT NULL DEFAULT '{}'")
    if "github_key_id" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN github_key_id TEXT NOT NULL DEFAULT ''")
    if "auto_sync_enabled" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN auto_sync_enabled INTEGER NOT NULL DEFAULT 1")
    if "render_fingerprint" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN render_fingerprint TEXT NOT NULL DEFAULT ''")
    if "visibility" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")
    if "share_enabled" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN share_enabled INTEGER NOT NULL DEFAULT 0")
    if "everyone_can_share" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN everyone_can_share INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE git_blog_blogs SET everyone_can_share=1 WHERE visibility='public'")
    conn.commit()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(get_settings().session_secret.encode()).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "无法解密 GitHub 凭据", status_code=400) from exc


def _normalize_repo(value: str) -> str:
    match = GITHUB_RE.fullmatch(value.strip())
    if not match:
        raise ToolboxError("INVALID_REPOSITORY", "仅支持 https://github.com/owner/repo 格式的仓库", status_code=400)
    return f"https://github.com/{match.group(1)}/{match.group(2)}.git"


def _normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise ToolboxError("INVALID_BLOG_SLUG", "博客地址只能使用小写字母、数字和连字符", status_code=400)
    return slug


def _normalize_root(value: str) -> str:
    raw = value.strip().strip("/")
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
        raise ToolboxError("INVALID_CONTENT_ROOT", "内容目录必须是仓库内的相对路径", status_code=400)
    return path.as_posix()


def _validate_branch(value: str) -> str:
    branch = value.strip()
    if not branch or branch.startswith("-") or any(x in branch for x in ("..", " ", "~", "^", ":", "?", "*", "[", "\\")):
        raise ToolboxError("INVALID_BRANCH", "分支名称不合法", status_code=400)
    return branch


def _defaults() -> dict[str, Any]:
    return {"site": {"description": "", "author": "", "language": "zh-CN", "theme": "auto", "customThemeId": "", "accentColor": "#42b983", "accentColorEnabled": False, "contentWidth": 820, "fontFamily": "system-ui, sans-serif", "postsPerPage": 10, "customTemplate": False}, "defaults": {"published": False, "author": "", "cover": ""}, "logs": {"accessEnabled": True, "recordIp": False, "recordUserAgent": True, "recordReferrer": True, "retentionDays": 30}}


def _normalise_config(value: dict[str, Any] | None) -> dict[str, Any]:
    result = _defaults()
    value = value or {}
    for section in ("site", "defaults", "logs"):
        incoming = value.get(section, {}) if isinstance(value, dict) else {}
        if isinstance(incoming, dict): result[section].update(incoming)
    site, defaults = result["site"], result["defaults"]
    site["theme"] = site["theme"] if site["theme"] in {"auto", "light", "dark"} else "auto"
    site["customThemeId"] = str(site.get("customThemeId") or "").strip()
    accent = str(site.get("accentColor") or "").strip().lower()
    site["accentColor"] = accent if re.fullmatch(r"#[0-9a-f]{6}", accent) else "#42b983"
    site["accentColorEnabled"] = bool(site.get("accentColorEnabled", False))
    site["postsPerPage"] = max(1, min(50, int(site.get("postsPerPage") or 10)))
    site["contentWidth"] = max(480, min(1400, int(site.get("contentWidth") or 820)))
    defaults["published"] = bool(defaults.get("published", False))
    logs = result["logs"]
    for key in ("accessEnabled", "recordIp", "recordUserAgent", "recordReferrer"):
        logs[key] = bool(logs.get(key, _defaults()["logs"][key]))
    logs["retentionDays"] = max(1, min(365, int(logs.get("retentionDays") or 30)))
    return result


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["config"] = json.loads(data.pop("config_json"))
    data["effectiveConfig"] = json.loads(data.pop("effective_config_json") or "{}")
    data["tokenConfigured"] = bool(data.pop("token_encrypted"))
    data["githubKeyId"] = data.pop("github_key_id")
    data["enabled"] = bool(data["enabled"])
    data["autoSyncEnabled"] = bool(data.pop("auto_sync_enabled"))
    data["syncIntervalMinutes"] = data.pop("sync_interval_minutes")
    data["contentRoot"] = data.pop("content_root")
    data["repoUrl"] = data.pop("repo_url")
    data["ownerUserId"] = data.pop("owner_user_id")
    data["currentCommit"] = data.pop("current_commit")
    data["renderFingerprint"] = data.pop("render_fingerprint")
    data["syncStatus"] = data.pop("sync_status")
    data["lastError"] = data.pop("last_error")
    data["visibility"] = data.get("visibility", "public")
    data["shareEnabled"] = bool(data.pop("share_enabled", 0))
    data["everyoneCanShare"] = bool(data.pop("everyone_can_share", 0))
    return data


def _owner_blog(blog_id: str, user: User) -> sqlite3.Row:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=? AND owner_user_id=?", (blog_id, user.id)).fetchone()
    if row is None: raise ToolboxError("BLOG_NOT_FOUND", "博客不存在", status_code=404)
    return row


def list_blogs(user: User) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM git_blog_blogs WHERE owner_user_id=? ORDER BY created_at DESC", (user.id,)).fetchall()
    return [_row(row) for row in rows]


def get_blog(blog_id: str, user: User) -> dict[str, Any]: return _row(_owner_blog(blog_id, user))


def _active_users() -> dict[str, User]:
    return {item.id: item for item in list_users() if not item.disabled}


def _visitor_hash(visitor_token: str) -> str:
    return hash_token(f"git-blog:{visitor_token}") if visitor_token else ""


def _access_password_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "label": row["label"], "canShare": bool(row["can_share"]),
        "enabled": bool(row["enabled"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def get_access_settings(blog_id: str, user: User) -> dict[str, Any]:
    blog = _owner_blog(blog_id, user)
    users = _active_users()
    with _conn() as conn:
        grants = conn.execute(
            "SELECT * FROM git_blog_access_users WHERE blog_id=? ORDER BY granted_at", (blog_id,)
        ).fetchall()
        passwords = conn.execute(
            "SELECT * FROM git_blog_access_passwords WHERE blog_id=? ORDER BY created_at", (blog_id,)
        ).fetchall()
    return {
        "visibility": blog["visibility"],
        "everyoneCanShare": bool(blog["everyone_can_share"]),
        "users": [
            {"userId": row["user_id"], "username": users[row["user_id"]].username,
             "displayName": users[row["user_id"]].display_name, "canShare": bool(row["can_share"])}
            for row in grants if row["user_id"] in users
        ],
        "passwords": [_access_password_public(row) for row in passwords],
    }


def set_blog_visibility(blog_id: str, visibility: str, user: User) -> dict[str, Any]:
    _owner_blog(blog_id, user)
    if visibility not in {"public", "private"}:
        raise ToolboxError("INVALID_VISIBILITY", "博客可见性不合法", status_code=400)
    with _conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET visibility=?,updated_at=? WHERE id=?", (visibility, _now(), blog_id))
        conn.commit()
    return get_access_settings(blog_id, user)


def add_access_user(blog_id: str, username: str, can_share: bool, owner: User) -> dict[str, Any]:
    _owner_blog(blog_id, owner)
    username = username.strip()
    target = next((item for item in _active_users().values() if item.username == username), None)
    if target is None:
        raise ToolboxError("USER_NOT_FOUND", "用户不存在或已被禁用", status_code=404)
    if target.id == owner.id:
        raise ToolboxError("OWNER_ALREADY_ALLOWED", "博客所有者始终拥有全部权限", status_code=400)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO git_blog_access_users(blog_id,user_id,can_share,granted_at) VALUES(?,?,?,?) "
            "ON CONFLICT(blog_id,user_id) DO UPDATE SET can_share=excluded.can_share",
            (blog_id, target.id, 1 if can_share else 0, _now()),
        )
        conn.commit()
    return get_access_settings(blog_id, owner)


def update_access_user(blog_id: str, user_id: str, can_share: bool, owner: User) -> dict[str, Any]:
    _owner_blog(blog_id, owner)
    with _conn() as conn:
        changed = conn.execute(
            "UPDATE git_blog_access_users SET can_share=? WHERE blog_id=? AND user_id=?",
            (1 if can_share else 0, blog_id, user_id),
        ).rowcount
        conn.commit()
    if not changed:
        raise ToolboxError("ACCESS_USER_NOT_FOUND", "受邀用户不存在", status_code=404)
    return get_access_settings(blog_id, owner)


def remove_access_user(blog_id: str, user_id: str, owner: User) -> dict[str, Any]:
    _owner_blog(blog_id, owner)
    with _conn() as conn:
        conn.execute("DELETE FROM git_blog_access_users WHERE blog_id=? AND user_id=?", (blog_id, user_id))
        conn.commit()
    return get_access_settings(blog_id, owner)


def add_access_password(blog_id: str, label: str, password: str, can_share: bool, owner: User) -> dict[str, Any]:
    _owner_blog(blog_id, owner)
    label, password = label.strip()[:100], password[:256]
    if not label or not password:
        raise ToolboxError("INVALID_ACCESS_PASSWORD", "密码名称和密码不能为空", status_code=400)
    salt = secrets.token_hex(16)
    with _conn() as conn:
        rows = conn.execute("SELECT password_hash,password_salt FROM git_blog_access_passwords WHERE blog_id=?", (blog_id,)).fetchall()
        if any(verify_password(password, row["password_salt"], row["password_hash"]) for row in rows):
            raise ToolboxError("DUPLICATE_ACCESS_PASSWORD", "该访问密码已存在", status_code=409)
        now, password_id = _now(), uuid4().hex
        conn.execute(
            "INSERT INTO git_blog_access_passwords(id,blog_id,label,password_hash,password_salt,can_share,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
            (password_id, blog_id, label, hash_password(password, salt), salt, 1 if can_share else 0, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM git_blog_access_passwords WHERE id=?", (password_id,)).fetchone()
    return _access_password_public(row)


def update_access_password(blog_id: str, password_id: str, payload: dict[str, Any], owner: User) -> dict[str, Any]:
    _owner_blog(blog_id, owner)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM git_blog_access_passwords WHERE id=? AND blog_id=?", (password_id, blog_id)).fetchone()
        if row is None:
            raise ToolboxError("ACCESS_PASSWORD_NOT_FOUND", "访问密码不存在", status_code=404)
        label = str(payload.get("label", row["label"])).strip()[:100]
        if not label:
            raise ToolboxError("INVALID_ACCESS_PASSWORD", "密码名称不能为空", status_code=400)
        can_share = 1 if payload.get("canShare", bool(row["can_share"])) else 0
        enabled = 1 if payload.get("enabled", bool(row["enabled"])) else 0
        new_password = str(payload.get("password") or "")[:256]
        password_hash_value, salt = row["password_hash"], row["password_salt"]
        revoke = not enabled
        if new_password:
            others = conn.execute("SELECT password_hash,password_salt FROM git_blog_access_passwords WHERE blog_id=? AND id<>?", (blog_id, password_id)).fetchall()
            if any(verify_password(new_password, item["password_salt"], item["password_hash"]) for item in others):
                raise ToolboxError("DUPLICATE_ACCESS_PASSWORD", "该访问密码已存在", status_code=409)
            salt = secrets.token_hex(16)
            password_hash_value = hash_password(new_password, salt)
            revoke = True
        conn.execute(
            "UPDATE git_blog_access_passwords SET label=?,password_hash=?,password_salt=?,can_share=?,enabled=?,updated_at=? WHERE id=?",
            (label, password_hash_value, salt, can_share, enabled, _now(), password_id),
        )
        if revoke:
            conn.execute("DELETE FROM git_blog_access_sessions WHERE password_id=?", (password_id,))
        conn.commit()
        changed = conn.execute("SELECT * FROM git_blog_access_passwords WHERE id=?", (password_id,)).fetchone()
    return _access_password_public(changed)


def remove_access_password(blog_id: str, password_id: str, owner: User) -> None:
    _owner_blog(blog_id, owner)
    with _conn() as conn:
        conn.execute("DELETE FROM git_blog_access_sessions WHERE password_id=?", (password_id,))
        changed = conn.execute("DELETE FROM git_blog_access_passwords WHERE id=? AND blog_id=?", (password_id, blog_id)).rowcount
        conn.commit()
    if not changed:
        raise ToolboxError("ACCESS_PASSWORD_NOT_FOUND", "访问密码不存在", status_code=404)


def blog_access(blog: dict[str, Any], user: User | None, visitor_token: str = "") -> dict[str, Any]:
    is_public = blog.get("visibility", "public") == "public"
    everyone_can_share = bool(blog.get("everyoneCanShare", False))
    if user and user.id == blog["ownerUserId"]:
        return {"allowed": True, "canShare": True, "kind": "owner", "label": user.username}
    if user:
        with _conn() as conn:
            grant = conn.execute("SELECT can_share FROM git_blog_access_users WHERE blog_id=? AND user_id=?", (blog["id"], user.id)).fetchone()
        if grant:
            return {"allowed": True, "canShare": everyone_can_share or bool(grant["can_share"]), "kind": "user", "label": user.username}
        if is_public:
            return {"allowed": True, "canShare": everyone_can_share, "kind": "user", "label": user.username}
    visitor_hash = _visitor_hash(visitor_token)
    if visitor_hash:
        with _conn() as conn:
            grant = conn.execute(
                "SELECT p.label,p.can_share FROM git_blog_access_sessions s JOIN git_blog_access_passwords p ON p.id=s.password_id "
                "WHERE s.blog_id=? AND s.visitor_hash=? AND s.expires_at>? AND p.enabled=1",
                (blog["id"], visitor_hash, _now()),
            ).fetchone()
        if grant:
            return {"allowed": True, "canShare": everyone_can_share or bool(grant["can_share"]), "kind": "password", "label": grant["label"]}
    return {"allowed": is_public, "canShare": is_public and everyone_can_share, "kind": "anonymous", "label": "匿名访客"}


def unlock_blog(blog: dict[str, Any], password: str, visitor_token: str) -> dict[str, Any]:
    if not password or not visitor_token:
        raise ToolboxError("INVALID_BLOG_PASSWORD", "访问密码错误", status_code=401)
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM git_blog_access_passwords WHERE blog_id=? AND enabled=1", (blog["id"],)).fetchall()
        matched = next((row for row in rows if verify_password(password, row["password_salt"], row["password_hash"])), None)
        if matched is None:
            raise ToolboxError("INVALID_BLOG_PASSWORD", "访问密码错误", status_code=401)
        expires = (datetime.now(timezone.utc) + timedelta(days=BLOG_ACCESS_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO git_blog_access_sessions(blog_id,visitor_hash,password_id,expires_at,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(blog_id,visitor_hash) DO UPDATE SET password_id=excluded.password_id,expires_at=excluded.expires_at,created_at=excluded.created_at",
            (blog["id"], _visitor_hash(visitor_token), matched["id"], expires, _now()),
        )
        conn.commit()
    return {"allowed": True, "canShare": bool(blog.get("everyoneCanShare")) or bool(matched["can_share"]), "label": matched["label"]}


def lock_blog(blog: dict[str, Any], visitor_token: str) -> None:
    visitor_hash = _visitor_hash(visitor_token)
    if not visitor_hash:
        return
    with _conn() as conn:
        conn.execute(
            "DELETE FROM git_blog_access_sessions WHERE blog_id=? AND visitor_hash=?",
            (blog["id"], visitor_hash),
        )
        conn.commit()


def _theme_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "filename": row["filename"],
        "size": len(row["css"]),
        "createdAt": row["created_at"],
    }


def list_themes(user: User) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM git_blog_themes WHERE owner_user_id=? ORDER BY created_at DESC",
            (user.id,),
        ).fetchall()
    return [_theme_row(row) for row in rows]


def save_theme(upload: Any, user: User) -> dict[str, Any]:
    filename = PurePosixPath(str(upload.filename or "").replace("\\", "/")).name
    if PurePosixPath(filename).suffix.lower() != ".css":
        raise ToolboxError("INVALID_THEME", "请选择 CSS 主题文件", status_code=400)
    name = PurePosixPath(filename).stem.strip()[:100]
    if not name:
        raise ToolboxError("INVALID_THEME", "主题文件名不能为空", status_code=400)
    raw = upload.file.read(MAX_THEME_BYTES + 1)
    if not raw:
        raise ToolboxError("INVALID_THEME", "主题 CSS 不能为空", status_code=400)
    if len(raw) > MAX_THEME_BYTES:
        raise ToolboxError("THEME_TOO_LARGE", "主题 CSS 不能超过 2 MiB", status_code=400)
    theme_id, created_at = uuid4().hex, _now()
    with _conn() as conn:
        duplicate = conn.execute(
            "SELECT 1 FROM git_blog_themes WHERE owner_user_id=? AND lower(name)=lower(?)",
            (user.id, name),
        ).fetchone()
        if duplicate:
            raise ToolboxError("THEME_NAME_EXISTS", "已存在同名主题", status_code=409)
        conn.execute(
            "INSERT INTO git_blog_themes(id,owner_user_id,name,filename,css,created_at) VALUES(?,?,?,?,?,?)",
            (theme_id,user.id,name,filename,sqlite3.Binary(raw),created_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM git_blog_themes WHERE id=?", (theme_id,)).fetchone()
    return _theme_row(row)


def theme_css(theme_id: str, user: User) -> bytes:
    with _conn() as conn:
        row = conn.execute(
            "SELECT css FROM git_blog_themes WHERE id=? AND owner_user_id=?",
            (theme_id,user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("THEME_NOT_FOUND", "主题不存在", status_code=404)
    return bytes(row["css"])


def delete_theme(theme_id: str, user: User) -> int:
    with _conn() as conn:
        theme = conn.execute(
            "SELECT 1 FROM git_blog_themes WHERE id=? AND owner_user_id=?",
            (theme_id,user.id),
        ).fetchone()
        if theme is None:
            raise ToolboxError("THEME_NOT_FOUND", "主题不存在", status_code=404)
        affected = 0
        rows = conn.execute(
            "SELECT id,config_json FROM git_blog_blogs WHERE owner_user_id=?",
            (user.id,),
        ).fetchall()
        for blog in rows:
            config = _normalise_config(json.loads(blog["config_json"]))
            if config["site"].get("customThemeId") != theme_id:
                continue
            config["site"]["customThemeId"] = ""
            conn.execute(
                "UPDATE git_blog_blogs SET config_json=?,sync_status=CASE WHEN current_commit<>'' THEN 'queued' ELSE sync_status END,next_sync_at=?,updated_at=? WHERE id=?",
                (json.dumps(config,ensure_ascii=False),_now(),_now(),blog["id"]),
            )
            affected += 1
        conn.execute("DELETE FROM git_blog_themes WHERE id=?", (theme_id,))
        conn.commit()
    return affected


def public_theme_css(blog: dict[str, Any]) -> bytes | None:
    theme_id = str((blog.get("config") or {}).get("site", {}).get("customThemeId") or "")
    if not theme_id:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT css FROM git_blog_themes WHERE id=? AND owner_user_id=?",
            (theme_id,blog["ownerUserId"]),
        ).fetchone()
    return bytes(row["css"]) if row else None


def _validate_custom_theme(config: dict[str, Any], user: User, conn: sqlite3.Connection) -> None:
    theme_id = config["site"].get("customThemeId", "")
    if not theme_id:
        return
    found = conn.execute(
        "SELECT 1 FROM git_blog_themes WHERE id=? AND owner_user_id=?",
        (theme_id, user.id),
    ).fetchone()
    if found is None:
        raise ToolboxError("THEME_NOT_FOUND", "所选自定义主题不存在", status_code=400)


def _render_fingerprint(blog: sqlite3.Row | dict[str, Any], commit: str | None = None) -> str:
    config = blog["config_json"]
    if isinstance(config, str):
        config = json.loads(config)
    payload = {
        "version": RENDER_PIPELINE_VERSION,
        "commit": (commit if commit is not None else blog["current_commit"]).lower(),
        "slug": blog["slug"],
        "contentRoot": blog["content_root"],
        "config": _normalise_config(config),
    }
    encoded = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_blog(payload: dict[str, Any], user: User) -> dict[str, Any]:
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM git_blog_blogs WHERE owner_user_id=?", (user.id,)).fetchone()[0]
        if count >= MAX_BLOGS_PER_USER: raise ToolboxError("BLOG_LIMIT", f"每个用户最多创建 {MAX_BLOGS_PER_USER} 个博客", status_code=400)
        now, blog_id = _now(), uuid4().hex
        slug, repo, branch = _normalize_slug(str(payload.get("slug") or "")), _normalize_repo(str(payload.get("repoUrl") or "")), _validate_branch(str(payload.get("branch") or "main"))
        name = str(payload.get("name") or "").strip() or slug
        interval = int(payload.get("syncIntervalMinutes") or 15)
        if not MIN_SYNC_MINUTES <= interval <= MAX_SYNC_MINUTES: raise ToolboxError("INVALID_INTERVAL", "同步周期必须在 5 到 1440 分钟之间", status_code=400)
        try:
            key_id=str(payload.get("githubKeyId") or "")
            if key_id: github_key_service.get_private_key(key_id,user)
            config = _normalise_config(payload.get("config"))
            _validate_custom_theme(config, user, conn)
            conn.execute("""INSERT INTO git_blog_blogs (id,owner_user_id,name,slug,repo_url,branch,content_root,sync_interval_minutes,config_json,github_key_id,auto_sync_enabled,next_sync_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (blog_id,user.id,name,slug,repo,branch,_normalize_root(str(payload.get("contentRoot") or "")),interval,json.dumps(config,ensure_ascii=False),key_id,1 if payload.get("autoSyncEnabled", True) else 0,now,now,now))
            conn.commit()
        except sqlite3.IntegrityError as exc: raise ToolboxError("BLOG_SLUG_EXISTS", "该博客地址已被占用", status_code=409) from exc
        row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=?", (blog_id,)).fetchone()
    return _row(row)


def update_blog(blog_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    old = _owner_blog(blog_id, user); values = dict(old)
    mapping = {"name":"name", "repoUrl":"repo_url", "branch":"branch", "contentRoot":"content_root", "syncIntervalMinutes":"sync_interval_minutes", "enabled":"enabled", "autoSyncEnabled":"auto_sync_enabled"}
    for public, db in mapping.items():
        if public not in payload: continue
        value = payload[public]
        if public == "repoUrl": value = _normalize_repo(str(value))
        elif public == "branch": value = _validate_branch(str(value))
        elif public == "contentRoot": value = _normalize_root(str(value))
        elif public == "syncIntervalMinutes":
            value = int(value)
            if not MIN_SYNC_MINUTES <= value <= MAX_SYNC_MINUTES: raise ToolboxError("INVALID_INTERVAL", "同步周期必须在 5 到 1440 分钟之间", status_code=400)
        elif public == "name":
            value = str(value).strip()
        elif public in {"enabled", "autoSyncEnabled"}: value = 1 if value else 0
        values[db] = value
    if "slug" in payload: values["slug"] = _normalize_slug(str(payload["slug"]))
    if not str(values["name"] or "").strip(): values["name"] = values["slug"]
    if "config" in payload: values["config_json"] = json.dumps(_normalise_config(payload["config"]), ensure_ascii=False)
    if "githubKeyId" in payload:
        key_id=str(payload.get("githubKeyId") or "")
        if key_id: github_key_service.get_private_key(key_id,user)
        values["github_key_id"] = key_id
    if values["current_commit"] and _render_fingerprint(values) != values["render_fingerprint"]:
        values["sync_status"] = "queued"
    values["updated_at"], values["next_sync_at"] = _now(), _now()
    with _conn() as conn:
        try:
            _validate_custom_theme(json.loads(values["config_json"]), user, conn)
            conn.execute("""UPDATE git_blog_blogs SET name=:name,slug=:slug,repo_url=:repo_url,branch=:branch,content_root=:content_root,sync_interval_minutes=:sync_interval_minutes,config_json=:config_json,github_key_id=:github_key_id,enabled=:enabled,auto_sync_enabled=:auto_sync_enabled,sync_status=:sync_status,updated_at=:updated_at,next_sync_at=:next_sync_at WHERE id=:id""", values); conn.commit()
        except sqlite3.IntegrityError as exc: raise ToolboxError("BLOG_SLUG_EXISTS", "该博客地址已被占用", status_code=409) from exc
        row=conn.execute("SELECT * FROM git_blog_blogs WHERE id=?",(blog_id,)).fetchone()
    return _row(row)


def delete_blog(blog_id: str, user: User) -> None:
    _owner_blog(blog_id,user)
    with _conn() as conn:
        share_ids = [row[0] for row in conn.execute("SELECT id FROM git_blog_shares WHERE blog_id=?", (blog_id,)).fetchall()]
        for share_id in share_ids:
            conn.execute("DELETE FROM git_blog_share_unlocks WHERE share_id=?", (share_id,))
            conn.execute("DELETE FROM git_blog_share_visitors WHERE share_id=?", (share_id,))
        for table in ("git_blog_access_users", "git_blog_access_passwords", "git_blog_access_sessions", "git_blog_shares", "git_blog_access_logs", "git_blog_assets", "git_blog_articles", "git_blog_runs", "git_blog_snapshots"):
            conn.execute(f"DELETE FROM {table} WHERE blog_id=?", (blog_id,))
        conn.execute("DELETE FROM git_blog_blogs WHERE id=?",(blog_id,)); conn.commit()
    shutil.rmtree(_root()/"snapshots"/blog_id, ignore_errors=True)


def request_sync(blog_id: str, user: User) -> None:
    _owner_blog(blog_id,user)
    with _conn() as conn: conn.execute("UPDATE git_blog_blogs SET next_sync_at=?, sync_status='queued', updated_at=? WHERE id=?",(_now(),_now(),blog_id)); conn.commit()


def list_runs(blog_id: str, user: User) -> list[dict[str, Any]]:
    _owner_blog(blog_id,user)
    with _conn() as conn: rows=conn.execute("SELECT * FROM git_blog_runs WHERE blog_id=? ORDER BY started_at DESC LIMIT 20",(blog_id,)).fetchall()
    return [{**dict(row),"warnings":json.loads(row["warnings_json"])} for row in rows]


def list_access_logs(blog_id: str, user: User) -> list[dict[str, Any]]:
    _owner_blog(blog_id, user)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM git_blog_access_logs WHERE blog_id=? ORDER BY requested_at DESC LIMIT 200",
            (blog_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_access(blog: dict[str, Any], request: Any, *, status_code: int, article_slug: str = "") -> None:
    """Store the configured subset of an anonymous public request."""
    logs = (blog.get("config") or {}).get("logs", {})
    if not logs.get("accessEnabled", True):
        return
    try:
        remote_address = request.client.host if logs.get("recordIp") and request.client else ""
        user_agent = request.headers.get("user-agent", "")[:500] if logs.get("recordUserAgent", True) else ""
        referrer = request.headers.get("referer", "")[:1000] if logs.get("recordReferrer", True) else ""
        retention = max(1, min(365, int(logs.get("retentionDays") or 30)))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention)).isoformat()
        with _conn() as conn:
            conn.execute(
                """INSERT INTO git_blog_access_logs(id,blog_id,requested_at,path,method,status_code,article_slug,remote_address,user_agent,referrer)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uuid4().hex, blog["id"], _now(), str(request.url.path)[:1000], request.method[:16], status_code, article_slug[:500], remote_address, user_agent, referrer),
            )
            conn.execute("DELETE FROM git_blog_access_logs WHERE blog_id=? AND requested_at<?", (blog["id"], cutoff))
    except Exception:
        # Visitor telemetry must never affect rendering a public blog page.
        return


def probe_repository(repo_url: str, user: User, github_key_id: str = "") -> dict[str, Any]:
    repo = _normalize_repo(repo_url); private_key=github_key_service.get_private_key(github_key_id,user) if github_key_id else ""; out = _git(["ls-remote", "--heads", _clone_url(repo, bool(private_key))], private_key=private_key, proxy_url=proxy_service.get_proxy_url_for_host("ssh.github.com" if private_key else "github.com"))
    branches = sorted(line.split("refs/heads/",1)[1] for line in out.splitlines() if "refs/heads/" in line)
    return {"repoUrl": repo, "branches": branches}


def _clone_url(repo_url: str, use_ssh: bool) -> str:
    if not use_ssh: return repo_url
    match=GITHUB_RE.fullmatch(repo_url)
    assert match
    # GitHub officially exposes SSH on port 443 as ssh.github.com.  This is
    # more reliable than port 22 on restricted corporate and campus networks.
    return f"git@ssh.github.com:{match.group(1)}/{match.group(2)}.git"

def _git(args: list[str], *, cwd: Path | None = None, private_key: str = "", proxy_url: str = "") -> str:
    env = os.environ.copy(); key_path: str | None = None
    if proxy_url:
        # Git's HTTP transport observes these variables.  For deploy-key SSH,
        # ProxyCommand below uses the same value without putting it in argv.
        env.update({"http_proxy": proxy_url, "https_proxy": proxy_url, "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url, "ALL_PROXY": proxy_url, "GIT_BLOG_PROXY_URL": proxy_url})
    if private_key:
        handle=tempfile.NamedTemporaryFile(mode="w",prefix="git-blog-key-",delete=False)
        handle.write(private_key); handle.close(); os.chmod(handle.name,0o600); key_path=handle.name
        command=f"ssh -i {shlex.quote(key_path)} -p 443 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        if proxy_url:
            tunnel = Path(__file__).with_name("proxy_tunnel.py")
            command += f" -o ProxyCommand={shlex.quote(f'{shlex.quote(sys.executable)} {shlex.quote(str(tunnel))} %h %p')}"
        env.update({"GIT_SSH_COMMAND":command, "GIT_TERMINAL_PROMPT":"0"})
    try:
        result=subprocess.run(["git",*args],cwd=cwd,env=env,text=True,capture_output=True,timeout=SYNC_TIMEOUT,check=False)
    except subprocess.TimeoutExpired as exc: raise ToolboxError("GIT_TIMEOUT", "Git 同步超时", status_code=504) from exc
    finally:
        if key_path: Path(key_path).unlink(missing_ok=True)
    if result.returncode: raise ToolboxError("GIT_FAILED", (result.stderr or result.stdout or "Git 命令失败").strip(), status_code=400)
    return result.stdout


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"): return {}, text
    parts=re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts)<3: raise ToolboxError("INVALID_FRONTMATTER", "Front matter 缺少结束标记", status_code=400)
    try: meta=yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc: raise ToolboxError("INVALID_FRONTMATTER", f"Front matter 无法解析: {exc}", status_code=400) from exc
    if not isinstance(meta,dict): raise ToolboxError("INVALID_FRONTMATTER", "Front matter 必须是对象", status_code=400)
    return meta,parts[2].lstrip("\r\n")


def _repo_config(snapshot: Path) -> dict[str, Any]:
    file=snapshot/".pansis-blog.yml"
    if not file.exists(): return {"site": {}, "defaults": {}}
    try: value=yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except (OSError,yaml.YAMLError) as exc: raise ToolboxError("INVALID_BLOG_CONFIG", f".pansis-blog.yml 无法解析: {exc}", status_code=400) from exc
    if not isinstance(value, dict):
        raise ToolboxError("INVALID_BLOG_CONFIG", ".pansis-blog.yml 必须是对象", status_code=400)
    normalised = _normalise_config(value)
    return {
        section: {key: normalised[section][key] for key in (value.get(section) or {}) if key in normalised[section]}
        for section in ("site", "defaults")
    }


def _merge_config(ui: dict[str,Any], repo: dict[str,Any]) -> dict[str,Any]:
    result=_normalise_config(ui)
    for key in ("site","defaults"): result[key].update(repo.get(key,{}) if isinstance(repo.get(key),dict) else {})
    return result


def _article_slug(source: str, declared: Any) -> str:
    raw=str(declared).strip().strip("/") if declared else str(PurePosixPath(source).with_suffix(""))
    path=PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts): raise ToolboxError("INVALID_ARTICLE_SLUG", f"文章 slug 不合法: {raw}", status_code=400)
    return path.as_posix()


def _plain(text: str) -> str:
    text = re.sub(r"```.*?```|\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"\$[^$\n]+\$|<[^>]+>|https?://\S+", " ", text)
    paragraphs = [re.sub(r"\s+", " ", re.sub(r"[`*_>#]", "", part)).strip() for part in re.split(r"\n\s*\n", text) if not re.match(r"^\s*#", part)]
    paragraphs = [part for part in paragraphs if len(part) >= 40]
    return max(paragraphs, key=len, default="")[:180]


def _render(markdown: str, *, source: str, source_map: dict[str,str], blog_slug: str) -> str:
    renderer=Path(__file__).resolve().parents[1]/"renderer"/"render.mjs"
    # A source checkout does not necessarily install nested tool dependencies.
    # Docker installs them at build time; this keeps local development equally
    # usable and avoids reporting a Markdown error for a missing package.
    if not (renderer.parents[1] / "node_modules" / "react" / "package.json").exists():
        try:
            subprocess.run(["npm", "ci", "--ignore-scripts"], cwd=renderer.parents[1], text=True, capture_output=True, timeout=180, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolboxError("RENDERER_DEPENDENCIES", "博客渲染依赖未安装，且自动安装失败", status_code=500) from exc
    payload={"markdown":markdown,"sourcePath":source,"sourceMap":source_map,"blogSlug":blog_slug}
    try:
        result=subprocess.run(["node",str(renderer)],input=json.dumps(payload),text=True,capture_output=True,timeout=30,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc: raise ToolboxError("MARKDOWN_RENDER_FAILED", "Markdown 渲染器不可用", status_code=500) from exc
    if result.returncode: raise ToolboxError("MARKDOWN_RENDER_FAILED", result.stderr.strip() or "Markdown 渲染失败", status_code=400)
    return json.loads(result.stdout)["html"]


def _snapshot_dir(blog_id: str, commit: str) -> Path: return _root()/"snapshots"/blog_id/commit


def _reconcile_snapshots(blog_id: str) -> None:
    """Register snapshots created before snapshot metadata was introduced."""
    snapshot_root = _root() / "snapshots" / blog_id
    if not snapshot_root.is_dir():
        return
    discovered: list[tuple[str, str, str]] = []
    for path in snapshot_root.iterdir():
        if path.is_dir() and re.fullmatch(r"[0-9a-fA-F]{40,64}", path.name):
            created_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            discovered.append((blog_id, path.name.lower(), created_at))
    if discovered:
        with _conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO git_blog_snapshots(blog_id,commit_hash,created_at) VALUES(?,?,?)",
                discovered,
            )
            conn.commit()


def _prune_snapshots(blog_id: str) -> None:
    with _conn() as conn:
        blog = conn.execute("SELECT current_commit FROM git_blog_blogs WHERE id=?", (blog_id,)).fetchone()
        current = blog["current_commit"] if blog else ""
        rows = conn.execute(
            "SELECT commit_hash FROM git_blog_snapshots WHERE blog_id=? ORDER BY created_at DESC, commit_hash DESC",
            (blog_id,),
        ).fetchall()
        commits = [row["commit_hash"] for row in rows]
        retained = commits[:MAX_SNAPSHOTS]
        if current and current in commits and current not in retained:
            retained = [*retained[:MAX_SNAPSHOTS - 1], current]
        stale = [commit for commit in commits if commit not in retained]
        if stale:
            conn.executemany(
                "DELETE FROM git_blog_snapshots WHERE blog_id=? AND commit_hash=?",
                [(blog_id, commit) for commit in stale],
            )
            conn.commit()
    for commit in stale:
        shutil.rmtree(_snapshot_dir(blog_id, commit), ignore_errors=True)


def list_snapshots(blog_id: str, user: User) -> list[dict[str, Any]]:
    blog = _owner_blog(blog_id, user)
    _reconcile_snapshots(blog_id)
    _prune_snapshots(blog_id)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT commit_hash,created_at FROM git_blog_snapshots WHERE blog_id=? ORDER BY CASE WHEN commit_hash=? THEN 0 ELSE 1 END, created_at DESC, commit_hash DESC LIMIT ?",
            (blog_id, blog["current_commit"], MAX_SNAPSHOTS),
        ).fetchall()
    return [
        {
            "commit": row["commit_hash"],
            "createdAt": row["created_at"],
            "current": row["commit_hash"] == blog["current_commit"],
        }
        for row in rows
    ]


def _prepare_snapshot(blog: sqlite3.Row, target: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    root = target / blog["content_root"]
    if not root.is_dir():
        raise ToolboxError("CONTENT_ROOT_NOT_FOUND", "内容目录不存在", status_code=400)
    files: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(target)
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in rel.parts) or not path.is_file():
            continue
        files.append(path)
        if len(files) > MAX_FILES:
            raise ToolboxError("REPOSITORY_LIMIT", "仓库文件数量超过限制", status_code=400)
    ui = json.loads(blog["config_json"])
    effective = _merge_config(ui, _repo_config(target))
    stamp = _git(["show", "-s", "--format=%cI", "HEAD"], cwd=target).strip() or _now()
    articles: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() not in {".md", ".markdown"}:
            continue
        if path.stat().st_size > MAX_MARKDOWN_BYTES:
            raise ToolboxError("MARKDOWN_LIMIT", f"文章过大: {path.name}", status_code=400)
        source = path.relative_to(root).as_posix()
        meta, body = _frontmatter(path.read_text(encoding="utf-8"))
        defaults = effective["defaults"]
        published = meta["published"] if "published" in meta else defaults["published"]
        if not isinstance(published, bool):
            raise ToolboxError("INVALID_FRONTMATTER", f"published 必须为布尔值: {source}", status_code=400)
        if not published:
            continue
        heading = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
        title = str(meta.get("title") or (heading.group(1) if heading else path.stem)).strip()
        slug = _article_slug(source, meta.get("slug"))
        summary = str(meta.get("summary") or _plain(body)).strip()[:180]
        tags = meta.get("tags", [])
        tags = [str(x).strip() for x in (tags if isinstance(tags, list) else [tags]) if str(x).strip()]
        articles.append({
            "source": source, "slug": slug, "title": title, "summary": summary,
            "author": str(meta.get("author") or defaults.get("author") or effective["site"].get("author") or ""),
            "date": str(meta.get("date") or stamp), "updated": str(meta.get("updated") or meta.get("date") or stamp),
            "tags": tags, "cover": str(meta.get("cover") or defaults.get("cover") or ""), "body": body,
        })
    source_map = {item["source"]: item["slug"] for item in articles}
    slugs = [item["slug"] for item in articles]
    if len(slugs) != len(set(slugs)):
        raise ToolboxError("DUPLICATE_ARTICLE_SLUG", "文章 slug 重复", status_code=400)
    assets: set[str] = set()
    asset_prefix = f'/blog/{blog["slug"]}/assets/'
    for item in articles:
        item["html"] = _render(item.pop("body"), source=item["source"], source_map=source_map, blog_slug=blog["slug"])
        for ref in re.findall(r'''(?:src|href)=["']([^"']+)["']''', item["html"]):
            if ref.startswith(asset_prefix):
                assets.add(unquote(ref[len(asset_prefix):]).split("#", 1)[0])
        if item["cover"]:
            assets.add(unquote(item["cover"].lstrip("/")))
    valid_assets: list[str] = []
    for asset in assets:
        candidate = (root / asset).resolve()
        if candidate.is_file() and root.resolve() in candidate.parents and candidate.stat().st_size <= MAX_ASSET_BYTES:
            valid_assets.append(asset)
    return effective, articles, valid_assets


def _replace_snapshot_index(conn: sqlite3.Connection, blog_id: str, effective: dict[str, Any], articles: list[dict[str, Any]], assets: list[str]) -> None:
    conn.execute("DELETE FROM git_blog_articles WHERE blog_id=?", (blog_id,))
    conn.execute("DELETE FROM git_blog_assets WHERE blog_id=?", (blog_id,))
    conn.executemany(
        "INSERT INTO git_blog_articles(blog_id,slug,source_path,title,summary,author,published_at,updated_at,tags_json,cover_path,html,plain_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(blog_id, a["slug"], a["source"], a["title"], a["summary"], a["author"], a["date"], a["updated"], json.dumps(a["tags"], ensure_ascii=False), a["cover"], a["html"], _plain(a["summary"])) for a in articles],
    )
    conn.executemany("INSERT INTO git_blog_assets(blog_id,path) VALUES(?,?)", [(blog_id, asset) for asset in assets])


def sync_due_blogs() -> None:
    now=_now()
    all_users={u.id:u for u in list_users()}
    with _conn() as conn:
        conn.execute("DELETE FROM git_blog_access_sessions WHERE expires_at<=?", (now,))
        conn.execute("DELETE FROM git_blog_share_unlocks WHERE expires_at<=?", (now,))
        orphan_ids=[row["id"] for row in conn.execute("SELECT id,owner_user_id FROM git_blog_blogs").fetchall() if row["owner_user_id"] not in all_users]
        rows=conn.execute("SELECT id FROM git_blog_blogs WHERE enabled=1 AND sync_status<>'syncing' AND sync_status NOT LIKE 'syncing_%' AND (sync_status='queued' OR (auto_sync_enabled=1 AND next_sync_at<=?)) ORDER BY next_sync_at LIMIT 3",(now,)).fetchall()
    for orphan_id in orphan_ids:
        with _conn() as conn:
            share_ids = [item[0] for item in conn.execute("SELECT id FROM git_blog_shares WHERE blog_id=?", (orphan_id,)).fetchall()]
            for share_id in share_ids:
                conn.execute("DELETE FROM git_blog_share_unlocks WHERE share_id=?", (share_id,))
                conn.execute("DELETE FROM git_blog_share_visitors WHERE share_id=?", (share_id,))
            for table in ("git_blog_access_users", "git_blog_access_passwords", "git_blog_access_sessions", "git_blog_shares", "git_blog_access_logs", "git_blog_assets", "git_blog_articles", "git_blog_runs", "git_blog_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE blog_id=?", (orphan_id,))
            conn.execute("DELETE FROM git_blog_blogs WHERE id=?",(orphan_id,)); conn.commit()
        shutil.rmtree(_root()/"snapshots"/orphan_id, ignore_errors=True)
    users={user_id:user for user_id,user in all_users.items() if not user.disabled}
    for row in rows:
        blog_id=row["id"]
        with _conn() as conn: owner=conn.execute("SELECT owner_user_id FROM git_blog_blogs WHERE id=?",(blog_id,)).fetchone()
        user=users.get(owner["owner_user_id"]) if owner else None
        if user is None or not can_access_tool(TOOL_ID,user):
            with _conn() as conn: conn.execute("UPDATE git_blog_blogs SET sync_status='paused', next_sync_at=? WHERE id=?",((datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(),blog_id)); conn.commit()
            continue
        sync_blog(blog_id)


def _set_sync_status(blog_id: str, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET sync_status=? WHERE id=?", (status,blog_id))
        conn.commit()


def sync_blog(blog_id: str) -> None:
    with _conn() as conn: blog=conn.execute("SELECT * FROM git_blog_blogs WHERE id=?",(blog_id,)).fetchone()
    if blog is None: return
    run_id, started=uuid4().hex,_now()
    with _conn() as conn:
        conn.execute("INSERT INTO git_blog_runs(id,blog_id,status,started_at) VALUES(?,?,?,?)",(run_id,blog_id,"running",started)); conn.execute("UPDATE git_blog_blogs SET sync_status='syncing_checking',last_attempt_at=?,last_error='' WHERE id=?",(started,blog_id)); conn.commit()
    warnings: list[str]=[]
    temp: Path | None = None
    target: Path | None = None
    target_created = False
    try:
        owner=next((u for u in list_users() if u.id==blog["owner_user_id"]),None)
        private_key=github_key_service.get_private_key(blog["github_key_id"],owner) if blog["github_key_id"] and owner else ""
        proxy_url=proxy_service.get_proxy_url_for_host("ssh.github.com" if private_key else "github.com")
        clone_url = _clone_url(blog["repo_url"], bool(private_key))
        remote_ref = f'refs/heads/{blog["branch"]}'
        remote_output = _git(
            ["ls-remote", "--heads", clone_url, remote_ref],
            private_key=private_key,
            proxy_url=proxy_url,
        )
        remote_commit = next(
            (line.split(None, 1)[0].lower() for line in remote_output.splitlines() if line.strip()),
            "",
        )
        if not remote_commit:
            raise ToolboxError("GIT_BRANCH_NOT_FOUND", f'远端分支不存在: {blog["branch"]}', status_code=400)
        next_time=(datetime.now(timezone.utc)+timedelta(minutes=int(blog["sync_interval_minutes"]))).isoformat()
        source_changed = blog["current_commit"].lower() != remote_commit
        target = _snapshot_dir(blog_id,remote_commit)
        snapshot_missing = not target.is_dir()
        expected_fingerprint = _render_fingerprint(blog,remote_commit)
        if not source_changed and not snapshot_missing and blog["render_fingerprint"] == expected_fingerprint:
            finished = _now()
            with _conn() as conn:
                conn.execute(
                    "UPDATE git_blog_blogs SET sync_status='success',last_error='',last_success_at=?,next_sync_at=?,updated_at=? WHERE id=?",
                    (finished,next_time,finished,blog_id),
                )
                conn.execute(
                    "UPDATE git_blog_runs SET status='success',commit_hash=?,message=?,warnings_json=?,finished_at=? WHERE id=?",
                    (remote_commit,"当前已是最新版本",json.dumps(warnings,ensure_ascii=False),finished,run_id),
                )
                conn.commit()
            return
        commit = remote_commit
        if source_changed or snapshot_missing:
            _set_sync_status(blog_id,"syncing_cloning")
            temp=Path(tempfile.mkdtemp(prefix="git-blog-",dir=_root()))
            checkout=temp/"repo"
            _git(["clone","--depth","1","--branch",blog["branch"],clone_url,str(checkout)],private_key=private_key,proxy_url=proxy_url)
            commit=_git(["rev-parse","HEAD"],cwd=checkout).strip().lower()
            source_changed = blog["current_commit"].lower() != commit
            target=_snapshot_dir(blog_id,commit); target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                shutil.move(str(checkout),str(target))
                target_created = True
            else: shutil.rmtree(temp,ignore_errors=True)
            _set_sync_status(blog_id,"syncing_rendering")
        else:
            _set_sync_status(blog_id,"syncing_rebuilding")
        effective, articles, valid_assets = _prepare_snapshot(blog, target)
        render_fingerprint = _render_fingerprint(blog,commit)
        if source_changed:
            message = f"远端发现新提交，已同步 {len(articles)} 篇文章"
        elif snapshot_missing:
            message = f"本地快照缺失，已重新同步 {len(articles)} 篇文章"
        else:
            message = f"仓库版本未变化，已应用博客配置更新，共 {len(articles)} 篇文章"
        _reconcile_snapshots(blog_id)
        _set_sync_status(blog_id,"syncing_publishing")
        with _conn() as conn:
            _replace_snapshot_index(conn, blog_id, effective, articles, valid_assets)
            conn.execute("INSERT OR IGNORE INTO git_blog_snapshots(blog_id,commit_hash,created_at) VALUES(?,?,?)", (blog_id,commit,_now()))
            conn.execute("UPDATE git_blog_blogs SET current_commit=?,render_fingerprint=?,effective_config_json=?,sync_status='success',last_error='',last_success_at=?,next_sync_at=?,updated_at=? WHERE id=?",(commit,render_fingerprint,json.dumps(effective,ensure_ascii=False),_now(),next_time,_now(),blog_id))
            conn.execute("UPDATE git_blog_runs SET status='success',commit_hash=?,message=?,warnings_json=?,finished_at=? WHERE id=?",(commit,message,json.dumps(warnings,ensure_ascii=False),_now(),run_id)); conn.commit()
        _prune_snapshots(blog_id)
    except Exception as exc:
        if target_created and target is not None:
            shutil.rmtree(target, ignore_errors=True)
        message=exc.message if isinstance(exc,ToolboxError) else str(exc)
        next_time=(datetime.now(timezone.utc)+timedelta(minutes=int(blog["sync_interval_minutes"]))).isoformat()
        with _conn() as conn:
            conn.execute("UPDATE git_blog_blogs SET sync_status='failed',last_error=?,next_sync_at=? WHERE id=?",(message,next_time,blog_id)); conn.execute("UPDATE git_blog_runs SET status='failed',message=?,finished_at=? WHERE id=?",(message,_now(),run_id)); conn.commit()
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)


def rollback_snapshot(blog_id: str, commit: str, user: User) -> dict[str, Any]:
    blog = _owner_blog(blog_id, user)
    commit = commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ToolboxError("INVALID_COMMIT", "快照提交哈希不合法", status_code=400)
    _reconcile_snapshots(blog_id)
    _prune_snapshots(blog_id)
    with _conn() as conn:
        known = conn.execute("SELECT 1 FROM git_blog_snapshots WHERE blog_id=? AND commit_hash=?", (blog_id, commit)).fetchone()
    target = _snapshot_dir(blog_id, commit)
    if known is None or not target.is_dir():
        raise ToolboxError("SNAPSHOT_NOT_FOUND", "快照不存在或已被清理", status_code=404)
    run_id, started = uuid4().hex, _now()
    with _conn() as conn:
        conn.execute("INSERT INTO git_blog_runs(id,blog_id,status,commit_hash,message,started_at) VALUES(?,?,?,?,?,?)", (run_id,blog_id,"running",commit,"正在回滚快照",started))
        conn.commit()
    try:
        effective, articles, assets = _prepare_snapshot(blog, target)
        render_fingerprint = _render_fingerprint(blog,commit)
        message = f"已回滚至 {commit[:8]}，自动同步已关闭"
        with _conn() as conn:
            _replace_snapshot_index(conn, blog_id, effective, articles, assets)
            conn.execute(
                "UPDATE git_blog_blogs SET current_commit=?,render_fingerprint=?,effective_config_json=?,auto_sync_enabled=0,sync_status='success',last_error='',updated_at=? WHERE id=?",
                (commit,render_fingerprint,json.dumps(effective,ensure_ascii=False),_now(),blog_id),
            )
            conn.execute("UPDATE git_blog_runs SET status='success',message=?,finished_at=? WHERE id=?", (message,_now(),run_id))
            conn.commit()
            updated = conn.execute("SELECT * FROM git_blog_blogs WHERE id=?", (blog_id,)).fetchone()
        return _row(updated)
    except Exception as exc:
        message = exc.message if isinstance(exc, ToolboxError) else str(exc)
        with _conn() as conn:
            conn.execute("UPDATE git_blog_runs SET status='failed',message=?,finished_at=? WHERE id=?", (message,_now(),run_id))
            conn.commit()
        if isinstance(exc, ToolboxError):
            raise
        raise ToolboxError("ROLLBACK_FAILED", message, status_code=500) from exc


def _share_public(row: sqlite3.Row, *, views: int = 0) -> dict[str, Any]:
    return {
        "id": row["id"], "token": row["token"], "url": f'/blog/share/{row["token"]}',
        "articleSlug": row["article_slug"], "mode": row["mode"],
        "passwordProtected": bool(row["password_hash"]), "expiresAt": row["expires_at"],
        "maxViews": row["max_views"], "views": views, "enabled": bool(row["enabled"]),
        "createdByType": row["created_by_type"], "createdByLabel": row["created_by_label"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def get_sharing_settings(blog_id: str, user: User) -> dict[str, Any]:
    blog = _owner_blog(blog_id, user)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT s.*,COUNT(v.visitor_hash) AS view_count FROM git_blog_shares s "
            "LEFT JOIN git_blog_share_visitors v ON v.share_id=s.id WHERE s.blog_id=? GROUP BY s.id ORDER BY s.created_at DESC",
            (blog_id,),
        ).fetchall()
    return {
        "enabled": bool(blog["share_enabled"]),
        "everyoneCanShare": bool(blog["everyone_can_share"]),
        "shares": [_share_public(row, views=int(row["view_count"])) for row in rows],
    }


def set_sharing_enabled(blog_id: str, enabled: bool, user: User, everyone_can_share: bool | None = None) -> dict[str, Any]:
    blog = _owner_blog(blog_id, user)
    everyone = bool(blog["everyone_can_share"]) if everyone_can_share is None else everyone_can_share
    with _conn() as conn:
        conn.execute(
            "UPDATE git_blog_blogs SET share_enabled=?,everyone_can_share=?,updated_at=? WHERE id=?",
            (1 if enabled else 0, 1 if everyone else 0, _now(), blog_id),
        )
        conn.commit()
    return get_sharing_settings(blog_id, user)


def _normalise_share_payload(payload: dict[str, Any], current: sqlite3.Row | None = None) -> dict[str, Any]:
    mode = str(payload.get("mode", current["mode"] if current else "document"))
    if mode not in {"document", "full"}:
        raise ToolboxError("INVALID_SHARE_MODE", "分享展示模式不合法", status_code=400)
    if "expiresAt" in payload:
        expires_at = payload.get("expiresAt") or None
    elif current:
        expires_at = current["expires_at"]
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    if expires_at:
        try:
            parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            expires_at = parsed.astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise ToolboxError("INVALID_SHARE_EXPIRY", "分享有效时间不合法", status_code=400) from exc
    max_views = payload.get("maxViews", current["max_views"] if current else None)
    if max_views in (None, ""):
        max_views = None
    else:
        max_views = int(max_views)
        if not 1 <= max_views <= 1_000_000:
            raise ToolboxError("INVALID_SHARE_VIEWS", "打开次数必须在 1 到 1000000 之间", status_code=400)
    return {"mode": mode, "expiresAt": expires_at, "maxViews": max_views}


def create_share(blog_slug: str, article_slug: str, payload: dict[str, Any], user: User | None, visitor_token: str) -> dict[str, Any]:
    blog = public_blog(blog_slug)
    if blog is None:
        raise ToolboxError("BLOG_NOT_FOUND", "博客不存在", status_code=404)
    if not blog["shareEnabled"]:
        raise ToolboxError("SHARING_DISABLED", "该博客尚未开启分享功能", status_code=403)
    principal = blog_access(blog, user, visitor_token)
    if not principal["canShare"]:
        raise ToolboxError("SHARE_PERMISSION_REQUIRED", "当前身份没有创建分享的权限", status_code=403)
    article_slug = article_slug.strip().strip("/")
    if public_article(blog["id"], article_slug) is None:
        raise ToolboxError("ARTICLE_NOT_FOUND", "文档不存在或尚未发布", status_code=404)
    options = _normalise_share_payload(payload)
    password = str(payload.get("password") or "")[:256]
    salt = secrets.token_hex(16) if password else ""
    now, share_id, token = _now(), uuid4().hex, secrets.token_urlsafe(32)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO git_blog_shares(id,token,blog_id,article_slug,mode,password_hash,password_salt,password_version,expires_at,max_views,enabled,created_by_type,created_by_label,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
            (share_id, token, blog["id"], article_slug, options["mode"], hash_password(password, salt) if password else "", salt,
             1 if password else 0, options["expiresAt"], options["maxViews"], principal["kind"], principal["label"], now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM git_blog_shares WHERE id=?", (share_id,)).fetchone()
    return _share_public(row)


def update_share(blog_id: str, share_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    _owner_blog(blog_id, user)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM git_blog_shares WHERE id=? AND blog_id=?", (share_id, blog_id)).fetchone()
        if row is None:
            raise ToolboxError("SHARE_NOT_FOUND", "分享链接不存在", status_code=404)
        options = _normalise_share_payload(payload, row)
        enabled = 1 if payload.get("enabled", bool(row["enabled"])) else 0
        password_hash_value, salt, version = row["password_hash"], row["password_salt"], int(row["password_version"])
        password_action = payload.get("passwordAction", "keep")
        if password_action == "remove":
            password_hash_value, salt, version = "", "", version + 1
        elif password_action == "replace":
            password = str(payload.get("password") or "")[:256]
            if not password:
                raise ToolboxError("INVALID_SHARE_PASSWORD", "新分享密码不能为空", status_code=400)
            salt, version = secrets.token_hex(16), version + 1
            password_hash_value = hash_password(password, salt)
        elif password_action != "keep":
            raise ToolboxError("INVALID_PASSWORD_ACTION", "分享密码操作不合法", status_code=400)
        conn.execute(
            "UPDATE git_blog_shares SET mode=?,password_hash=?,password_salt=?,password_version=?,expires_at=?,max_views=?,enabled=?,updated_at=? WHERE id=?",
            (options["mode"], password_hash_value, salt, version, options["expiresAt"], options["maxViews"], enabled, _now(), share_id),
        )
        if version != int(row["password_version"]):
            conn.execute("DELETE FROM git_blog_share_unlocks WHERE share_id=?", (share_id,))
        conn.commit()
        changed = conn.execute("SELECT * FROM git_blog_shares WHERE id=?", (share_id,)).fetchone()
        views = conn.execute("SELECT COUNT(*) FROM git_blog_share_visitors WHERE share_id=?", (share_id,)).fetchone()[0]
    return _share_public(changed, views=views)


def delete_share(blog_id: str, share_id: str, user: User) -> None:
    _owner_blog(blog_id, user)
    with _conn() as conn:
        conn.execute("DELETE FROM git_blog_share_unlocks WHERE share_id=?", (share_id,))
        conn.execute("DELETE FROM git_blog_share_visitors WHERE share_id=?", (share_id,))
        changed = conn.execute("DELETE FROM git_blog_shares WHERE id=? AND blog_id=?", (share_id, blog_id)).rowcount
        conn.commit()
    if not changed:
        raise ToolboxError("SHARE_NOT_FOUND", "分享链接不存在", status_code=404)


def _share_row(token: str) -> tuple[sqlite3.Row, dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM git_blog_shares WHERE token=?", (token,)).fetchone()
        blog_row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=? AND enabled=1 AND current_commit<>''", (row["blog_id"],)).fetchone() if row else None
    if row is None or blog_row is None:
        raise ToolboxError("SHARE_NOT_FOUND", "分享链接不存在", status_code=404)
    blog = _row(blog_row)
    owner = next((item for item in list_users() if item.id == blog["ownerUserId"] and not item.disabled), None)
    if owner is None or not can_access_tool(TOOL_ID, owner):
        raise ToolboxError("SHARE_NOT_FOUND", "分享链接不存在", status_code=404)
    if not row["enabled"] or not blog["shareEnabled"]:
        raise ToolboxError("SHARE_UNAVAILABLE", "分享链接已停用", status_code=410)
    if row["expires_at"] and row["expires_at"] <= _now():
        raise ToolboxError("SHARE_EXPIRED", "分享链接已过期", status_code=410)
    return row, blog


def unlock_share(token: str, password: str, visitor_token: str) -> None:
    row, _blog = _share_row(token)
    if not row["password_hash"] or not visitor_token or not verify_password(password, row["password_salt"], row["password_hash"]):
        raise ToolboxError("INVALID_SHARE_PASSWORD", "分享密码错误", status_code=401)
    expiry = datetime.now(timezone.utc) + timedelta(days=BLOG_ACCESS_DAYS)
    if row["expires_at"]:
        expiry = min(expiry, datetime.fromisoformat(row["expires_at"]))
    with _conn() as conn:
        conn.execute(
            "INSERT INTO git_blog_share_unlocks(share_id,visitor_hash,password_version,expires_at) VALUES(?,?,?,?) "
            "ON CONFLICT(share_id,visitor_hash) DO UPDATE SET password_version=excluded.password_version,expires_at=excluded.expires_at",
            (row["id"], _visitor_hash(visitor_token), row["password_version"], expiry.isoformat()),
        )
        conn.commit()


def open_share(token: str, user: User | None, visitor_token: str, *, count_view: bool = True) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    row, blog = _share_row(token)
    visitor_hash = _visitor_hash(visitor_token)
    if row["password_hash"]:
        with _conn() as conn:
            unlocked = conn.execute(
                "SELECT 1 FROM git_blog_share_unlocks WHERE share_id=? AND visitor_hash=? AND password_version=? AND expires_at>?",
                (row["id"], visitor_hash, row["password_version"], _now()),
            ).fetchone()
        if unlocked is None:
            raise ToolboxError("SHARE_PASSWORD_REQUIRED", "请输入分享密码", status_code=401)
    if row["mode"] == "full" and blog["visibility"] == "private" and not blog_access(blog, user, visitor_token)["allowed"]:
        raise ToolboxError("BLOG_LOGIN_REQUIRED", "该私密博客需要认证", status_code=401)
    article = public_article(blog["id"], row["article_slug"])
    if article is None:
        raise ToolboxError("SHARE_ARTICLE_UNAVAILABLE", "分享的文档已不存在或取消发布", status_code=410)
    if count_view:
        if not visitor_hash:
            raise ToolboxError("VISITOR_REQUIRED", "无法建立浏览器访问标识", status_code=400)
        with _conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT 1 FROM git_blog_share_visitors WHERE share_id=? AND visitor_hash=?", (row["id"], visitor_hash)).fetchone()
            if existing:
                conn.execute("UPDATE git_blog_share_visitors SET last_opened_at=? WHERE share_id=? AND visitor_hash=?", (_now(), row["id"], visitor_hash))
            else:
                views = conn.execute("SELECT COUNT(*) FROM git_blog_share_visitors WHERE share_id=?", (row["id"],)).fetchone()[0]
                if row["max_views"] is not None and views >= row["max_views"]:
                    raise ToolboxError("SHARE_VIEW_LIMIT", "分享链接的打开次数已用完", status_code=410)
                now = _now()
                conn.execute("INSERT INTO git_blog_share_visitors(share_id,visitor_hash,first_opened_at,last_opened_at) VALUES(?,?,?,?)", (row["id"], visitor_hash, now, now))
            conn.commit()
    return _share_public(row), blog, article


def public_blog(slug: str) -> dict[str,Any] | None:
    with _conn() as conn: row=conn.execute("SELECT * FROM git_blog_blogs WHERE slug=? AND enabled=1 AND current_commit<>''",(slug,)).fetchone()
    if row is None: return None
    owner=next((u for u in list_users() if u.id==row["owner_user_id"] and not u.disabled),None)
    return _row(row) if owner and can_access_tool(TOOL_ID,owner) else None


def public_articles(blog_id:str, *, page:int=1, tag:str="", query:str="", path_prefix:str="") -> tuple[list[dict[str,Any]],int]:
    where=["blog_id=?"]; args:list[Any]=[blog_id]
    if tag: where.append("tags_json LIKE ?"); args.append(f'%"{tag}"%')
    if query: where.append("(title LIKE ? OR summary LIKE ? OR plain_text LIKE ?)"); args.extend([f"%{query}%"]*3)
    if path_prefix: where.append("source_path LIKE ?"); args.append(f"{path_prefix.rstrip('/')}%")
    clause=" AND ".join(where)
    with _conn() as conn:
        total=conn.execute(f"SELECT COUNT(*) FROM git_blog_articles WHERE {clause}",args).fetchone()[0]
        rows=conn.execute(f"SELECT * FROM git_blog_articles WHERE {clause} ORDER BY published_at DESC LIMIT 50 OFFSET ?",[ *args,max(0,page-1)*50]).fetchall()
    return [_article(row) for row in rows],total


def _article(row:sqlite3.Row)->dict[str,Any]:
    value=dict(row); value["tags"]=json.loads(value.pop("tags_json")); value["publishedAt"]=value.pop("published_at"); value["updatedAt"]=value.pop("updated_at"); value["sourcePath"]=value.pop("source_path"); value["coverPath"]=value.pop("cover_path"); return value


def public_article(blog_id:str, slug:str)->dict[str,Any]|None:
    with _conn() as conn: row=conn.execute("SELECT * FROM git_blog_articles WHERE blog_id=? AND slug=?",(blog_id,slug)).fetchone()
    return _article(row) if row else None


def public_tags(blog_id:str)->list[str]:
    with _conn() as conn: rows=conn.execute("SELECT tags_json FROM git_blog_articles WHERE blog_id=?",(blog_id,)).fetchall()
    return sorted({tag for row in rows for tag in json.loads(row["tags_json"])},key=str.lower)


def public_tag_counts(blog_id: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    with _conn() as conn: rows = conn.execute("SELECT tags_json FROM git_blog_articles WHERE blog_id=?", (blog_id,)).fetchall()
    for row in rows:
        for tag in json.loads(row["tags_json"]): counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": tag, "count": counts[tag]} for tag in sorted(counts, key=str.lower)]


def public_directory(blog_id: str) -> dict[str, Any]:
    root: dict[str, Any] = {"name": "", "children": {}, "article": None}
    with _conn() as conn: rows = conn.execute("SELECT source_path,slug,title FROM git_blog_articles WHERE blog_id=? ORDER BY source_path", (blog_id,)).fetchall()
    for row in rows:
        node = root
        parts = PurePosixPath(row["source_path"]).with_suffix("").parts
        for part in parts:
            node = node["children"].setdefault(part, {"name": part, "children": {}, "article": None})
        node["article"] = {"slug": row["slug"], "title": row["title"]}
    def serialise(node: dict[str, Any]) -> dict[str, Any]:
        return {"name": node["name"], "article": node["article"], "children": [serialise(child) for _, child in sorted(node["children"].items(), key=lambda pair: pair[0].lower())]}
    return serialise(root)


def template_dir(blog_id: str) -> Path: return _root() / "templates" / blog_id


def save_template(blog_id: str, archive: Any, user: User) -> None:
    blog = _owner_blog(blog_id, user)
    raw = archive.file.read(MAX_TEMPLATE_BYTES + 1)
    if len(raw) > MAX_TEMPLATE_BYTES: raise ToolboxError("TEMPLATE_TOO_LARGE", "模板包超过 10 MiB", status_code=400)
    try: bundle = zipfile.ZipFile(__import__("io").BytesIO(raw))
    except zipfile.BadZipFile as exc: raise ToolboxError("INVALID_TEMPLATE", "模板必须是 ZIP 文件", status_code=400) from exc
    entries = [entry for entry in bundle.infolist() if not entry.is_dir()]
    if len(entries) > MAX_TEMPLATE_FILES: raise ToolboxError("TEMPLATE_TOO_MANY_FILES", "模板文件数量超过限制", status_code=400)
    allowed = {".css", ".woff", ".woff2", ".ttf", ".otf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    if not any(PurePosixPath(entry.filename).suffix.lower() == ".css" for entry in entries): raise ToolboxError("INVALID_TEMPLATE", "模板包必须包含 CSS 文件", status_code=400)
    target = template_dir(blog_id); temporary = Path(tempfile.mkdtemp(prefix="git-blog-template-", dir=_root()))
    try:
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in allowed: raise ToolboxError("INVALID_TEMPLATE", "模板包含不支持或不安全的文件", status_code=400)
            output = temporary / path.as_posix(); output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, output.open("wb") as destination: shutil.copyfileobj(source, destination)
        shutil.rmtree(target, ignore_errors=True); target.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(temporary), str(target))
        config = _normalise_config(json.loads(blog["config_json"])); config["site"]["customTemplate"] = True
        with _conn() as conn: conn.execute("UPDATE git_blog_blogs SET config_json=?,updated_at=? WHERE id=?", (json.dumps(config, ensure_ascii=False), _now(), blog_id)); conn.commit()
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True); raise


def delete_template(blog_id: str, user: User) -> None:
    blog = _owner_blog(blog_id, user); shutil.rmtree(template_dir(blog_id), ignore_errors=True)
    config = _normalise_config(json.loads(blog["config_json"])); config["site"]["customTemplate"] = False
    with _conn() as conn: conn.execute("UPDATE git_blog_blogs SET config_json=?,updated_at=? WHERE id=?", (json.dumps(config, ensure_ascii=False), _now(), blog_id)); conn.commit()


def public_asset(blog:dict[str,Any], path:str)->Path|None:
    cleaned=path.lstrip("/")
    with _conn() as conn: allowed=conn.execute("SELECT 1 FROM git_blog_assets WHERE blog_id=? AND path=?",(blog["id"],cleaned)).fetchone()
    candidate=(_snapshot_dir(blog["id"],blog["currentCommit"])/blog["contentRoot"]/cleaned).resolve()
    root=_snapshot_dir(blog["id"],blog["currentCommit"]).resolve()
    return candidate if allowed and candidate.is_file() and root in candidate.parents else None
