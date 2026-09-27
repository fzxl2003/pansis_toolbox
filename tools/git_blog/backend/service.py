from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import yaml
from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User, list_users
from backend.app.services import github_key_service
from backend.app.services import proxy_service
from backend.app.services.tool_access_service import can_access_tool

TOOL_ID = "git_blog"
MAX_BLOGS_PER_USER = 10
MIN_SYNC_MINUTES, MAX_SYNC_MINUTES = 5, 1440
MAX_FILES, MAX_MARKDOWN_BYTES, MAX_ASSET_BYTES = 10_000, 2 * 1024 * 1024, 25 * 1024 * 1024
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
        enabled INTEGER NOT NULL DEFAULT 1, current_commit TEXT NOT NULL DEFAULT '',
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
      CREATE TABLE IF NOT EXISTS git_blog_access_logs (
        id TEXT PRIMARY KEY, blog_id TEXT NOT NULL, requested_at TEXT NOT NULL,
        path TEXT NOT NULL, method TEXT NOT NULL, status_code INTEGER NOT NULL,
        article_slug TEXT NOT NULL DEFAULT '', remote_address TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT ''
      );
      CREATE INDEX IF NOT EXISTS idx_git_blog_articles_date ON git_blog_articles(blog_id, published_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_runs_blog ON git_blog_runs(blog_id, started_at DESC);
      CREATE INDEX IF NOT EXISTS idx_git_blog_access_logs_blog ON git_blog_access_logs(blog_id, requested_at DESC);
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(git_blog_blogs)").fetchall()}
    if "effective_config_json" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN effective_config_json TEXT NOT NULL DEFAULT '{}'")
    if "github_key_id" not in columns:
        conn.execute("ALTER TABLE git_blog_blogs ADD COLUMN github_key_id TEXT NOT NULL DEFAULT ''")
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
    return {"site": {"description": "", "author": "", "language": "zh-CN", "theme": "auto", "accentColor": "#2563eb", "contentWidth": 820, "fontFamily": "system-ui, sans-serif", "postsPerPage": 10}, "defaults": {"published": False, "author": "", "cover": ""}, "logs": {"accessEnabled": True, "recordIp": False, "recordUserAgent": True, "recordReferrer": True, "retentionDays": 30}}


def _normalise_config(value: dict[str, Any] | None) -> dict[str, Any]:
    result = _defaults()
    value = value or {}
    for section in ("site", "defaults", "logs"):
        incoming = value.get(section, {}) if isinstance(value, dict) else {}
        if isinstance(incoming, dict): result[section].update(incoming)
    site, defaults = result["site"], result["defaults"]
    site["theme"] = site["theme"] if site["theme"] in {"auto", "light", "dark"} else "auto"
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
    data["syncIntervalMinutes"] = data.pop("sync_interval_minutes")
    data["contentRoot"] = data.pop("content_root")
    data["repoUrl"] = data.pop("repo_url")
    data["ownerUserId"] = data.pop("owner_user_id")
    data["currentCommit"] = data.pop("current_commit")
    data["syncStatus"] = data.pop("sync_status")
    data["lastError"] = data.pop("last_error")
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
            conn.execute("""INSERT INTO git_blog_blogs (id,owner_user_id,name,slug,repo_url,branch,content_root,sync_interval_minutes,config_json,github_key_id,next_sync_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (blog_id,user.id,name,slug,repo,branch,_normalize_root(str(payload.get("contentRoot") or "")),interval,json.dumps(_normalise_config(payload.get("config")),ensure_ascii=False),key_id,now,now,now))
            conn.commit()
        except sqlite3.IntegrityError as exc: raise ToolboxError("BLOG_SLUG_EXISTS", "该博客地址已被占用", status_code=409) from exc
        row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=?", (blog_id,)).fetchone()
    return _row(row)


def update_blog(blog_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    old = _owner_blog(blog_id, user); values = dict(old)
    mapping = {"name":"name", "repoUrl":"repo_url", "branch":"branch", "contentRoot":"content_root", "syncIntervalMinutes":"sync_interval_minutes", "enabled":"enabled"}
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
        elif public == "enabled": value = 1 if value else 0
        values[db] = value
    if "slug" in payload: values["slug"] = _normalize_slug(str(payload["slug"]))
    if not str(values["name"] or "").strip(): values["name"] = values["slug"]
    if "config" in payload: values["config_json"] = json.dumps(_normalise_config(payload["config"]), ensure_ascii=False)
    if "githubKeyId" in payload:
        key_id=str(payload.get("githubKeyId") or "")
        if key_id: github_key_service.get_private_key(key_id,user)
        values["github_key_id"] = key_id
    values["updated_at"], values["next_sync_at"] = _now(), _now()
    with _conn() as conn:
        try:
            conn.execute("""UPDATE git_blog_blogs SET name=:name,slug=:slug,repo_url=:repo_url,branch=:branch,content_root=:content_root,sync_interval_minutes=:sync_interval_minutes,config_json=:config_json,github_key_id=:github_key_id,enabled=:enabled,updated_at=:updated_at,next_sync_at=:next_sync_at WHERE id=:id""", values); conn.commit()
        except sqlite3.IntegrityError as exc: raise ToolboxError("BLOG_SLUG_EXISTS", "该博客地址已被占用", status_code=409) from exc
        row=conn.execute("SELECT * FROM git_blog_blogs WHERE id=?",(blog_id,)).fetchone()
    return _row(row)


def delete_blog(blog_id: str, user: User) -> None:
    _owner_blog(blog_id,user)
    with _conn() as conn:
        conn.execute("DELETE FROM git_blog_assets WHERE blog_id=?",(blog_id,)); conn.execute("DELETE FROM git_blog_articles WHERE blog_id=?",(blog_id,)); conn.execute("DELETE FROM git_blog_runs WHERE blog_id=?",(blog_id,)); conn.execute("DELETE FROM git_blog_blogs WHERE id=?",(blog_id,)); conn.commit()
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
    if result.returncode: raise ToolboxError("GIT_FAILED", (result.stderr or result.stdout or "Git 命令失败").strip()[:1000], status_code=400)
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


def _plain(text: str) -> str: return re.sub(r"\s+", " ", re.sub(r"[`*_#>\[\]()]", "", text)).strip()


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
    if result.returncode: raise ToolboxError("MARKDOWN_RENDER_FAILED", result.stderr.strip()[:1000] or "Markdown 渲染失败", status_code=400)
    return json.loads(result.stdout)["html"]


def _snapshot_dir(blog_id: str, commit: str) -> Path: return _root()/"snapshots"/blog_id/commit


def sync_due_blogs() -> None:
    now=_now()
    all_users={u.id:u for u in list_users()}
    with _conn() as conn:
        orphan_ids=[row["id"] for row in conn.execute("SELECT id,owner_user_id FROM git_blog_blogs").fetchall() if row["owner_user_id"] not in all_users]
        rows=conn.execute("SELECT id FROM git_blog_blogs WHERE enabled=1 AND next_sync_at<=? ORDER BY next_sync_at LIMIT 3",(now,)).fetchall()
    for orphan_id in orphan_ids:
        with _conn() as conn:
            conn.execute("DELETE FROM git_blog_assets WHERE blog_id=?",(orphan_id,)); conn.execute("DELETE FROM git_blog_articles WHERE blog_id=?",(orphan_id,)); conn.execute("DELETE FROM git_blog_runs WHERE blog_id=?",(orphan_id,)); conn.execute("DELETE FROM git_blog_blogs WHERE id=?",(orphan_id,)); conn.commit()
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


def sync_blog(blog_id: str) -> None:
    with _conn() as conn: blog=conn.execute("SELECT * FROM git_blog_blogs WHERE id=?",(blog_id,)).fetchone()
    if blog is None: return
    run_id, started=uuid4().hex,_now()
    with _conn() as conn:
        conn.execute("INSERT INTO git_blog_runs(id,blog_id,status,started_at) VALUES(?,?,?,?)",(run_id,blog_id,"running",started)); conn.execute("UPDATE git_blog_blogs SET sync_status='syncing',last_attempt_at=?,last_error='' WHERE id=?",(started,blog_id)); conn.commit()
    warnings: list[str]=[]
    try:
        temp=Path(tempfile.mkdtemp(prefix="git-blog-",dir=_root()))
        checkout=temp/"repo"; owner=next((u for u in list_users() if u.id==blog["owner_user_id"]),None)
        private_key=github_key_service.get_private_key(blog["github_key_id"],owner) if blog["github_key_id"] and owner else ""
        proxy_url=proxy_service.get_proxy_url_for_host("ssh.github.com" if private_key else "github.com")
        _git(["clone","--depth","1","--branch",blog["branch"],_clone_url(blog["repo_url"],bool(private_key)),str(checkout)],private_key=private_key,proxy_url=proxy_url)
        commit=_git(["rev-parse","HEAD"],cwd=checkout).strip(); stamp=_git(["show","-s","--format=%cI","HEAD"],cwd=checkout).strip() or _now()
        target=_snapshot_dir(blog_id,commit); target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): shutil.move(str(checkout),str(target))
        else: shutil.rmtree(temp,ignore_errors=True)
        root=target/blog["content_root"]
        if not root.is_dir(): raise ToolboxError("CONTENT_ROOT_NOT_FOUND", "内容目录不存在",status_code=400)
        files=[]
        for path in root.rglob("*"):
            rel=path.relative_to(target)
            if any(part in EXCLUDED_DIRS or part.startswith(".") for part in rel.parts) or not path.is_file(): continue
            files.append(path)
            if len(files)>MAX_FILES: raise ToolboxError("REPOSITORY_LIMIT", "仓库文件数量超过限制",status_code=400)
        ui=json.loads(blog["config_json"]); effective=_merge_config(ui,_repo_config(target)); articles=[]
        for path in files:
            if path.suffix.lower() not in {".md",".markdown"}: continue
            if path.stat().st_size>MAX_MARKDOWN_BYTES: raise ToolboxError("MARKDOWN_LIMIT",f"文章过大: {path.name}",status_code=400)
            source=path.relative_to(root).as_posix(); meta,body=_frontmatter(path.read_text(encoding="utf-8")); defaults=effective["defaults"]
            published=meta["published"] if "published" in meta else defaults["published"]
            if not isinstance(published,bool): raise ToolboxError("INVALID_FRONTMATTER",f"published 必须为布尔值: {source}",status_code=400)
            if not published: continue
            heading=re.search(r"^#\s+(.+?)\s*$",body,re.MULTILINE)
            title=str(meta.get("title") or (heading.group(1) if heading else path.stem)).strip()
            slug=_article_slug(source,meta.get("slug")); summary=str(meta.get("summary") or _plain(body).split(". ",1)[0][:240]).strip()
            tags=meta.get("tags",[]); tags=[str(x).strip() for x in (tags if isinstance(tags,list) else [tags]) if str(x).strip()]
            articles.append({"source":source,"slug":slug,"title":title,"summary":summary,"author":str(meta.get("author") or defaults.get("author") or effective["site"].get("author") or ""),"date":str(meta.get("date") or stamp),"updated":str(meta.get("updated") or meta.get("date") or stamp),"tags":tags,"cover":str(meta.get("cover") or defaults.get("cover") or ""),"body":body})
        source_map={item["source"]:item["slug"] for item in articles}
        slugs=[item["slug"] for item in articles]
        if len(slugs)!=len(set(slugs)): raise ToolboxError("DUPLICATE_ARTICLE_SLUG","文章 slug 重复",status_code=400)
        assets:set[str]=set()
        asset_prefix=f'/blog/{blog["slug"]}/assets/'
        for item in articles:
            item["html"]=_render(item.pop("body"),source=item["source"],source_map=source_map,blog_slug=blog["slug"])
            for ref in re.findall(r'''(?:src|href)=["']([^"']+)["']''',item["html"]):
                if ref.startswith(asset_prefix): assets.add(unquote(ref[len(asset_prefix):]).split("#",1)[0])
            if item["cover"]: assets.add(unquote(item["cover"].lstrip("/")))
        valid_assets=[]
        for asset in assets:
            candidate=(root/asset).resolve()
            if candidate.is_file() and root.resolve() in candidate.parents and candidate.stat().st_size<=MAX_ASSET_BYTES: valid_assets.append(asset)
        next_time=(datetime.now(timezone.utc)+timedelta(minutes=int(blog["sync_interval_minutes"]))).isoformat()
        with _conn() as conn:
            conn.execute("DELETE FROM git_blog_articles WHERE blog_id=?",(blog_id,)); conn.execute("DELETE FROM git_blog_assets WHERE blog_id=?",(blog_id,))
            conn.executemany("INSERT INTO git_blog_articles(blog_id,slug,source_path,title,summary,author,published_at,updated_at,tags_json,cover_path,html,plain_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[(blog_id,a["slug"],a["source"],a["title"],a["summary"],a["author"],a["date"],a["updated"],json.dumps(a["tags"],ensure_ascii=False),a["cover"],a["html"],_plain(a["summary"])) for a in articles])
            conn.executemany("INSERT INTO git_blog_assets(blog_id,path) VALUES(?,?)",[(blog_id,a) for a in valid_assets])
            conn.execute("UPDATE git_blog_blogs SET current_commit=?,effective_config_json=?,sync_status='success',last_error='',last_success_at=?,next_sync_at=?,updated_at=? WHERE id=?",(commit,json.dumps(effective,ensure_ascii=False),_now(),next_time,_now(),blog_id))
            conn.execute("UPDATE git_blog_runs SET status='success',commit_hash=?,message=?,warnings_json=?,finished_at=? WHERE id=?",(commit,f"同步 {len(articles)} 篇文章",json.dumps(warnings,ensure_ascii=False),_now(),run_id)); conn.commit()
    except Exception as exc:
        message=exc.message if isinstance(exc,ToolboxError) else str(exc)
        next_time=(datetime.now(timezone.utc)+timedelta(minutes=int(blog["sync_interval_minutes"]))).isoformat()
        with _conn() as conn:
            conn.execute("UPDATE git_blog_blogs SET sync_status='failed',last_error=?,next_sync_at=? WHERE id=?",(message[:2000],next_time,blog_id)); conn.execute("UPDATE git_blog_runs SET status='failed',message=?,finished_at=? WHERE id=?",(message[:2000],_now(),run_id)); conn.commit()


def public_blog(slug: str) -> dict[str,Any] | None:
    with _conn() as conn: row=conn.execute("SELECT * FROM git_blog_blogs WHERE slug=? AND enabled=1 AND current_commit<>''",(slug,)).fetchone()
    if row is None: return None
    owner=next((u for u in list_users() if u.id==row["owner_user_id"] and not u.disabled),None)
    return _row(row) if owner and can_access_tool(TOOL_ID,owner) else None


def public_articles(blog_id:str, *, page:int=1, tag:str="", query:str="") -> tuple[list[dict[str,Any]],int]:
    where=["blog_id=?"]; args:list[Any]=[blog_id]
    if tag: where.append("tags_json LIKE ?"); args.append(f'%"{tag}"%')
    if query: where.append("(title LIKE ? OR summary LIKE ? OR plain_text LIKE ?)"); args.extend([f"%{query}%"]*3)
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


def public_asset(blog:dict[str,Any], path:str)->Path|None:
    cleaned=path.lstrip("/")
    with _conn() as conn: allowed=conn.execute("SELECT 1 FROM git_blog_assets WHERE blog_id=? AND path=?",(blog["id"],cleaned)).fetchone()
    candidate=(_snapshot_dir(blog["id"],blog["currentCommit"])/blog["contentRoot"]/cleaned).resolve()
    root=_snapshot_dir(blog["id"],blog["currentCommit"]).resolve()
    return candidate if allowed and candidate.is_file() and root in candidate.parents else None
