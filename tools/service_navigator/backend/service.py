from __future__ import annotations

import concurrent.futures
import html
import ipaddress
import json
import logging
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler, urlopen
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User, hash_password, hash_token, list_users, verify_password
from backend.app.services.email_service import send_email as platform_send_email
from backend.app.services.tool_access_service import can_access_tool

TOOL_ID = "service_navigator"
logger = logging.getLogger(__name__)
MAX_TARGETS = 32
MAX_DNS_ADDRESSES = 8
ACCESS_DAYS = 30
VISITOR_DAYS = 365
SCAN_SEMAPHORE = threading.BoundedSemaphore(3)
ACTIVE_RUNS: set[str] = set()
ACTIVE_LOCK = threading.Lock()
RECOVERY_LOCK = threading.Lock()
RECOVERY_DONE = False
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
WEB_PORTS = {80, 81, 443, 444, 591, 593, 8000, 8008, 8080, 8081, 8088, 8443, 8888, 9000, 9090}
# This is deliberately maintained in Python rather than delegated to a system
# scanner.  It covers registered/well-known TCP ports; users can add higher or
# application-specific ports on individual targets.
COMMON_TCP_PORTS = tuple(range(1, 1001))
PORT_CONNECT_TIMEOUT = 2.0
PORT_PROBE_TIMEOUT = 1.0
HOST_SCAN_TIMEOUT = 300.0
PORT_SCAN_WORKERS = 100
HTTP_METADATA_LIMIT = 1024 * 1024
FAVICON_LIMIT = 512 * 1024
HEALTH_DEFAULT_INTERVAL = 300
HEALTH_MIN_INTERVAL = 60
HEALTH_MAX_INTERVAL = 86400
HEALTH_CONFIRM_DEFAULT = 3
HEALTH_REPEAT_DEFAULT = 3600
HEALTH_RETENTION_DAYS = 30
HEALTH_TIMEOUT = 5.0
HEALTH_SEMAPHORE = threading.BoundedSemaphore(10)
HEALTH_ACTIVE_SITES: set[str] = set()
HEALTH_ACTIVE_LOCK = threading.Lock()
HEALTH_COMPAT_DATABASES: set[str] = set()
HEALTH_COMPAT_LOCK = threading.Lock()
PORT_SERVICE_HINTS = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "domain", 110: "pop3",
    111: "rpcbind", 119: "nntp", 135: "msrpc", 139: "netbios-ssn", 143: "imap",
    389: "ldap", 443: "https", 445: "microsoft-ds", 465: "smtps", 514: "shell",
    587: "submission", 631: "ipp", 636: "ldaps", 993: "imaps", 995: "pop3s",
    1080: "socks", 1433: "ms-sql-s", 1521: "oracle", 2049: "nfs", 2375: "docker",
    2376: "docker", 3000: "http", 3306: "mysql", 3389: "ms-wbt-server", 4000: "http",
    5000: "http", 5432: "postgresql", 5672: "amqp", 5900: "vnc", 6379: "redis",
    6443: "https", 7001: "http", 8000: "http", 8008: "http", 8080: "http",
    8081: "http", 8088: "http", 8443: "https", 8888: "http", 9000: "http",
    9090: "http", 9200: "http", 11211: "memcached", 27017: "mongodb",
}
COMMAND_TEMPLATES = {
    "ssh": "ssh -p {port} <user>@{host}",
    "sftp": "sftp -P {port} <user>@{host}",
    "mysql": "mysql -h {host} -P {port} -u <user> -p",
    "postgresql": "psql -h {host} -p {port} -U <user>",
    "redis": "redis-cli -h {host} -p {port}",
    "mongodb": "mongosh mongodb://{host}:{port}",
    "ftp": "ftp {host} {port}",
}
NAV_BREAKPOINTS = (16, 12, 8, 4)
NAV_SIZES: dict[str, tuple[int, int]] = {
    "small": (1, 1), "medium": (2, 2), "large": (4, 4), "wide": (4, 1),
}
NAV_ICON_LIMIT = 1024 * 1024
NAV_ICON_SUFFIXES = {".ico", ".png", ".jpg", ".jpeg", ".webp"}
BACKGROUND_LIMIT = 6 * 1024 * 1024
BACKGROUND_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def root_dir() -> Path:
    path = get_settings().storage_dir / "data" / "tools" / TOOL_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def icon_dir() -> Path:
    path = root_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def navigation_icon_dir() -> Path:
    path = root_dir() / "navigation-icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def background_dir() -> Path:
    path = root_dir() / "backgrounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return root_dir() / "data.db"


def conn() -> sqlite3.Connection:
    # Health checks may finish at the same time. Let SQLite wait briefly for a
    # peer's very short write transaction instead of dropping that snapshot.
    database = sqlite3.connect(db_path(), timeout=30)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    init_database(database)
    return database


def init_database(database: sqlite3.Connection) -> None:
    global RECOVERY_DONE
    database.executescript(
        """
        CREATE TABLE IF NOT EXISTS service_navigator_sites (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
          slug TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
          visibility TEXT NOT NULL DEFAULT 'private', background_source TEXT NOT NULL DEFAULT 'default',
          theme TEXT NOT NULL DEFAULT 'auto', accent_color TEXT NOT NULL DEFAULT '#4f7cff',
          background_filename TEXT NOT NULL DEFAULT '', show_all_services INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_targets (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, label TEXT NOT NULL, address TEXT NOT NULL,
          custom_ports TEXT NOT NULL DEFAULT '', show_in_navigation INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(site_id, address), FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_services (
          id TEXT PRIMARY KEY, target_id TEXT NOT NULL, protocol TEXT NOT NULL DEFAULT 'tcp', port INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'online', service_name TEXT NOT NULL DEFAULT 'unknown',
          product TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', extra_info TEXT NOT NULL DEFAULT '',
          resolved_addresses_json TEXT NOT NULL DEFAULT '[]', http_title TEXT NOT NULL DEFAULT '',
          favicon_filename TEXT NOT NULL DEFAULT '', detected_url TEXT NOT NULL DEFAULT '',
          display_name TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
          navigation_url TEXT NOT NULL DEFAULT '', connection_command TEXT NOT NULL DEFAULT '',
          service_type TEXT NOT NULL DEFAULT 'port',
          visible INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
          health_enabled INTEGER NOT NULL DEFAULT 1, health_url TEXT NOT NULL DEFAULT '',
          health_status TEXT NOT NULL DEFAULT 'unknown', last_health_checked_at TEXT,
          last_health_status_code INTEGER, last_health_latency_ms INTEGER, last_health_error TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(target_id, protocol, port), FOREIGN KEY(target_id) REFERENCES service_navigator_targets(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_scan_runs (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, target_id TEXT, status TEXT NOT NULL,
          requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT NOT NULL DEFAULT '',
          summary_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE,
          FOREIGN KEY(target_id) REFERENCES service_navigator_targets(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_access_users (
          site_id TEXT NOT NULL, user_id TEXT NOT NULL, granted_at TEXT NOT NULL,
          PRIMARY KEY(site_id, user_id), FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_access_passwords (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, label TEXT NOT NULL, password_hash TEXT NOT NULL,
          password_salt TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_access_sessions (
          site_id TEXT NOT NULL, visitor_hash TEXT NOT NULL, password_id TEXT NOT NULL,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(site_id, visitor_hash), FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE,
          FOREIGN KEY(password_id) REFERENCES service_navigator_access_passwords(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_health_settings (
          site_id TEXT PRIMARY KEY, check_interval_seconds INTEGER NOT NULL DEFAULT 300,
          email_recipients_json TEXT NOT NULL DEFAULT '[]', confirm_count INTEGER NOT NULL DEFAULT 3,
          repeat_interval_seconds INTEGER NOT NULL DEFAULT 3600, max_repeat_count INTEGER NOT NULL DEFAULT 0,
          last_checked_at TEXT, updated_at TEXT NOT NULL,
          FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_health_snapshots (
          id TEXT PRIMARY KEY, service_id TEXT NOT NULL, checked_at TEXT NOT NULL,
          status TEXT NOT NULL, status_code INTEGER, latency_ms INTEGER, final_url TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '', manual INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY(service_id) REFERENCES service_navigator_services(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_health_alert_states (
          service_id TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0,
          is_alerting INTEGER NOT NULL DEFAULT 0, last_alerted_at TEXT, repeat_count INTEGER NOT NULL DEFAULT 0,
          repeat_exhausted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
          FOREIGN KEY(service_id) REFERENCES service_navigator_services(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_health_events (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, service_id TEXT NOT NULL, event_type TEXT NOT NULL,
          message TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
          FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE,
          FOREIGN KEY(service_id) REFERENCES service_navigator_services(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_pages (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, name TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0, visible INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_items (
          id TEXT PRIMARY KEY, page_id TEXT NOT NULL, name TEXT NOT NULL,
          size TEXT NOT NULL DEFAULT 'small', icon_source TEXT NOT NULL DEFAULT 'none',
          icon_filename TEXT NOT NULL DEFAULT '', icon_text TEXT NOT NULL DEFAULT '', icon_color TEXT NOT NULL DEFAULT '#4f7cff', favicon_service_id TEXT,
          preference_revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(page_id) REFERENCES service_navigator_nav_pages(id) ON DELETE CASCADE,
          FOREIGN KEY(favicon_service_id) REFERENCES service_navigator_services(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_item_services (
          item_id TEXT NOT NULL, service_id TEXT NOT NULL,
          PRIMARY KEY(item_id, service_id),
          FOREIGN KEY(item_id) REFERENCES service_navigator_nav_items(id) ON DELETE CASCADE,
          FOREIGN KEY(service_id) REFERENCES service_navigator_services(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_item_layouts (
          item_id TEXT NOT NULL, breakpoint INTEGER NOT NULL, grid_x INTEGER NOT NULL, grid_y INTEGER NOT NULL,
          PRIMARY KEY(item_id, breakpoint),
          FOREIGN KEY(item_id) REFERENCES service_navigator_nav_items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_service_navigator_targets_site ON service_navigator_targets(site_id);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_services_target ON service_navigator_services(target_id, port);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_runs_site ON service_navigator_scan_runs(site_id, requested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_health_snapshots_service ON service_navigator_health_snapshots(service_id, checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_health_events_site ON service_navigator_health_events(site_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_nav_pages_site ON service_navigator_nav_pages(site_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_nav_items_page ON service_navigator_nav_items(page_id);
        """
    )
    _ensure_column(database, "service_navigator_services", "health_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_services", "service_type", "TEXT NOT NULL DEFAULT 'port'")
    _ensure_column(database, "service_navigator_services", "health_url", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_services", "health_status", "TEXT NOT NULL DEFAULT 'unknown'")
    _ensure_column(database, "service_navigator_services", "last_health_checked_at", "TEXT")
    _ensure_column(database, "service_navigator_services", "last_health_status_code", "INTEGER")
    _ensure_column(database, "service_navigator_services", "last_health_latency_ms", "INTEGER")
    _ensure_column(database, "service_navigator_services", "last_health_error", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_sites", "background_source", "TEXT NOT NULL DEFAULT 'default'")
    _ensure_column(database, "service_navigator_sites", "background_filename", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_sites", "show_all_services", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_sites", "theme", "TEXT NOT NULL DEFAULT 'auto'")
    _ensure_column(database, "service_navigator_sites", "accent_color", "TEXT NOT NULL DEFAULT '#4f7cff'")
    _ensure_column(database, "service_navigator_nav_items", "icon_text", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_nav_items", "icon_color", "TEXT NOT NULL DEFAULT '#4f7cff'")
    _ensure_column(database, "service_navigator_targets", "show_in_navigation", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_nav_pages", "visible", "INTEGER NOT NULL DEFAULT 1")
    # Services created before health checks existed have the column default of
    # one. Do this compatibility write only once per database so simultaneous
    # health result writes do not contend for SQLite's write lock.
    database_key = str(db_path())
    with HEALTH_COMPAT_LOCK:
        needs_health_compat = database_key not in HEALTH_COMPAT_DATABASES
        if needs_health_compat:
            HEALTH_COMPAT_DATABASES.add(database_key)
    if needs_health_compat:
        # Backfill settings for navigation sites created before health
        # monitoring was introduced, so the scheduler can discover them
        # without an owner first opening the management page.
        database.execute("""INSERT OR IGNORE INTO service_navigator_health_settings(site_id,updated_at)
            SELECT id, ? FROM service_navigator_sites""", (now_iso(),))
        database.execute("""UPDATE service_navigator_services SET health_enabled=0
            WHERE last_health_checked_at IS NULL AND lower(service_name) NOT LIKE '%http%'
            AND port NOT IN (80,81,443,444,591,593,8000,8008,8080,8081,8088,8443,8888,9000,9090)""")
        database.execute("""UPDATE service_navigator_services SET service_type='http'
            WHERE service_type='port' AND (lower(service_name) LIKE '%http%' OR port IN
            (80,81,443,444,591,593,8000,8008,8080,8081,8088,8443,8888,9000,9090))""")
    with RECOVERY_LOCK:
        if not RECOVERY_DONE:
            database.execute("UPDATE service_navigator_scan_runs SET status='interrupted', finished_at=?, error='服务重启导致扫描中断' WHERE status IN ('queued','running')", (now_iso(),))
            RECOVERY_DONE = True
    database.commit()


def _ensure_column(database: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in database.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        database.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _site_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {
        "id": item["id"], "title": item["title"], "slug": item["slug"], "description": item["description"],
        "visibility": item["visibility"], "backgroundSource": item.get("background_source", "default"),
        "backgroundFilename": item.get("background_filename", ""), "showAllServices": bool(item.get("show_all_services", 1)),
        "theme": _normalise_site_theme(item.get("theme", "auto")), "accentColor": _normalise_accent_color(item.get("accent_color", "#4f7cff")),
        "createdAt": item["created_at"], "updatedAt": item["updated_at"],
    }


def _target_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {"id": item["id"], "label": item["label"], "address": item["address"], "customPorts": item["custom_ports"], "showInNavigation": bool(item.get("show_in_navigation", 1)), "createdAt": item["created_at"], "updatedAt": item["updated_at"]}


def _service_public(row: sqlite3.Row | dict[str, Any], *, include_fingerprint: bool = True) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    output = {
        "id": item["id"], "targetId": item["target_id"], "protocol": item["protocol"], "port": int(item["port"]),
        "state": item["state"], "serviceName": item["service_name"], "httpTitle": item["http_title"],
        "detectedUrl": item["detected_url"], "displayName": item["display_name"],
        "description": item["description"], "navigationUrl": item["navigation_url"], "connectionCommand": item["connection_command"],
        "serviceType": item["service_type"],
        "faviconUrl": "",
        "healthEnabled": bool(item["health_enabled"]), "healthUrl": item["health_url"],
        "healthStatus": item["health_status"], "lastHealthCheckedAt": item["last_health_checked_at"],
        "lastHealthStatusCode": item["last_health_status_code"], "lastHealthLatencyMs": item["last_health_latency_ms"],
        "lastHealthError": item["last_health_error"],
        "firstSeenAt": item["first_seen_at"], "lastSeenAt": item["last_seen_at"], "updatedAt": item["updated_at"],
    }
    if item["favicon_filename"]:
        output["faviconUrl"] = f"/service-nav/icon/{item['id']}"
    if include_fingerprint:
        output.update({"product": item["product"], "version": item["version"], "extraInfo": item["extra_info"], "resolvedAddresses": json.loads(item["resolved_addresses_json"] or "[]")})
    return output


def _run_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "targetId": row["target_id"], "status": row["status"], "requestedAt": row["requested_at"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "error": row["error"], "summary": json.loads(row["summary_json"] or "{}")}


def normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise ToolboxError("INVALID_SLUG", "站点地址仅支持小写字母、数字和短横线，长度为 1–63", status_code=400, tool_id=TOOL_ID)
    return slug


def normalize_address(value: str) -> str:
    address = value.strip().rstrip(".")
    if not address or "://" in address or "/" in address or "*" in address or " " in address:
        raise ToolboxError("INVALID_TARGET", "目标必须是单个 IP 地址或域名", status_code=400, tool_id=TOOL_ID)
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", address):
            raise ToolboxError("INVALID_TARGET", "IPv4 地址格式不合法", status_code=400, tool_id=TOOL_ID)
        if not HOST_RE.fullmatch(address):
            raise ToolboxError("INVALID_TARGET", "目标必须是合法 IP 地址或域名", status_code=400, tool_id=TOOL_ID)
        return address.lower()


def normalize_ports(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    ports: set[int] = set()
    for chunk in raw.split(","):
        part = chunk.strip()
        if not part:
            raise ToolboxError("INVALID_PORTS", "端口列表格式不合法", status_code=400, tool_id=TOOL_ID)
        if "-" in part:
            bits = part.split("-", 1)
            if len(bits) != 2 or not bits[0].isdigit() or not bits[1].isdigit():
                raise ToolboxError("INVALID_PORTS", "端口区间格式不合法", status_code=400, tool_id=TOOL_ID)
            start, end = int(bits[0]), int(bits[1])
            if start < 1 or end > 65535 or end < start:
                raise ToolboxError("INVALID_PORTS", "端口必须在 1–65535 之间", status_code=400, tool_id=TOOL_ID)
            ports.update(range(start, end + 1))
        elif part.isdigit() and 1 <= int(part) <= 65535:
            ports.add(int(part))
        else:
            raise ToolboxError("INVALID_PORTS", "端口必须在 1–65535 之间", status_code=400, tool_id=TOOL_ID)
    return _compact_port_ranges(ports)


def _normalise_site_theme(value: Any) -> str:
    theme = str(value or "auto").lower()
    return theme if theme in {"auto", "light", "dark"} else "auto"


def _normalise_accent_color(value: Any) -> str:
    color = str(value or "").strip().lower()
    return color if re.fullmatch(r"#[0-9a-f]{6}", color) else "#4f7cff"


def _compact_port_ranges(ports: set[int]) -> str:
    """Persist consecutive custom ports as ranges instead of a huge CSV."""
    ordered = sorted(ports)
    ranges: list[str] = []
    index = 0
    while index < len(ordered):
        start = end = ordered[index]
        while index + 1 < len(ordered) and ordered[index + 1] == end + 1:
            index += 1
            end = ordered[index]
        ranges.append(str(start) if start == end else f"{start}-{end}")
        index += 1
    return ",".join(ranges)


def _owner_site(user: User, *, required: bool = True) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_sites WHERE owner_user_id=?", (user.id,)).fetchone()
    if row is None and required:
        raise ToolboxError("SITE_NOT_FOUND", "尚未创建服务导航站", status_code=404, tool_id=TOOL_ID)
    return _row(row) if row else None


def get_site(user: User) -> dict[str, Any] | None:
    site = _owner_site(user, required=False)
    if not site:
        return None
    return site_detail(site["id"], user)


def create_site(payload: dict[str, Any], user: User) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()[:100]
    if not title:
        raise ToolboxError("INVALID_SITE", "站点标题不能为空", status_code=400, tool_id=TOOL_ID)
    slug = normalize_slug(str(payload.get("slug") or ""))
    description = str(payload.get("description") or "").strip()[:500]
    theme = _normalise_site_theme(payload.get("theme", "auto"))
    accent_color = _normalise_accent_color(payload.get("accentColor", "#4f7cff"))
    now = now_iso()
    with conn() as database:
        if database.execute("SELECT 1 FROM service_navigator_sites WHERE owner_user_id=?", (user.id,)).fetchone():
            raise ToolboxError("SITE_EXISTS", "每个账号只能创建一个服务导航站", status_code=409, tool_id=TOOL_ID)
        try:
            site_id = uuid4().hex
            database.execute("""INSERT INTO service_navigator_sites
                (id,owner_user_id,title,slug,description,visibility,theme,accent_color,created_at,updated_at)
                VALUES(?,?,?,?,?,'private',?,?,?,?)""", (site_id, user.id, title, slug, description, theme, accent_color, now, now))
            database.execute("INSERT INTO service_navigator_health_settings(site_id,updated_at) VALUES(?,?)", (site_id, now))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("SLUG_EXISTS", "该站点地址已被占用", status_code=409, tool_id=TOOL_ID) from exc
    return get_site(user) or {}


def update_site(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    title = str(payload.get("title", site["title"]) or "").strip()[:100]
    if not title:
        raise ToolboxError("INVALID_SITE", "站点标题不能为空", status_code=400, tool_id=TOOL_ID)
    slug = normalize_slug(str(payload.get("slug", site["slug"]) or ""))
    description = str(payload.get("description", site["description"]) or "").strip()[:500]
    theme = _normalise_site_theme(payload.get("theme", site.get("theme", "auto")))
    accent_color = _normalise_accent_color(payload.get("accentColor", site.get("accent_color", "#4f7cff")))
    with conn() as database:
        try:
            database.execute("UPDATE service_navigator_sites SET title=?,slug=?,description=?,theme=?,accent_color=?,updated_at=? WHERE id=?", (title, slug, description, theme, accent_color, now_iso(), site["id"]))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("SLUG_EXISTS", "该站点地址已被占用", status_code=409, tool_id=TOOL_ID) from exc
    return get_site(user) or {}


def delete_site(user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        filenames = [row[0] for row in database.execute("SELECT favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=?", (site["id"],)).fetchall()]
        navigation_filenames = [row[0] for row in database.execute("""SELECT i.icon_filename FROM service_navigator_nav_items i
            JOIN service_navigator_nav_pages p ON p.id=i.page_id WHERE p.site_id=? AND i.icon_filename<>''""", (site["id"],)).fetchall()]
        background_filename = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()[0]
        database.execute("DELETE FROM service_navigator_sites WHERE id=?", (site["id"],))
        database.commit()
    for filename in filenames:
        _remove_icon(filename)
    for filename in navigation_filenames:
        _remove_navigation_icon(filename)
    _remove_background(background_filename)


def site_detail(site_id: str, user: User) -> dict[str, Any]:
    with conn() as database:
        site_row = database.execute("SELECT * FROM service_navigator_sites WHERE id=? AND owner_user_id=?", (site_id, user.id)).fetchone()
        if not site_row:
            raise ToolboxError("SITE_NOT_FOUND", "服务导航站不存在", status_code=404, tool_id=TOOL_ID)
        targets = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site_id,)).fetchall()
        services = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.display_name,s.port,s.id", (site_id,)).fetchall()
        runs = database.execute("SELECT * FROM service_navigator_scan_runs WHERE site_id=? ORDER BY requested_at DESC LIMIT 20", (site_id,)).fetchall()
    return {"site": _site_public(site_row), "targets": [_target_public(row) for row in targets], "services": [_service_public(row) for row in services], "runs": [_run_public(row) for row in runs], "navigation": _navigation_detail(site_id)}


# ---------------------------------------------------------------------------
# Editable visitor navigation
# ---------------------------------------------------------------------------

def _navigation_detail(site_id: str) -> dict[str, Any]:
    """Owner-only layout data. Positions are deliberately stored per fixed grid."""
    with conn() as database:
        pages = database.execute("SELECT * FROM service_navigator_nav_pages WHERE site_id=? ORDER BY sort_order,id", (site_id,)).fetchall()
        items = database.execute("""SELECT i.* FROM service_navigator_nav_items i
            JOIN service_navigator_nav_pages p ON p.id=i.page_id WHERE p.site_id=? ORDER BY i.created_at,i.id""", (site_id,)).fetchall()
        services = database.execute("""SELECT s.* FROM service_navigator_services s
            JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=?""", (site_id,)).fetchall()
        links = database.execute("""SELECT l.item_id,l.service_id FROM service_navigator_nav_item_services l
            JOIN service_navigator_nav_items i ON i.id=l.item_id JOIN service_navigator_nav_pages p ON p.id=i.page_id
            WHERE p.site_id=?""", (site_id,)).fetchall()
        layouts = database.execute("""SELECT l.* FROM service_navigator_nav_item_layouts l
            JOIN service_navigator_nav_items i ON i.id=l.item_id JOIN service_navigator_nav_pages p ON p.id=i.page_id
            WHERE p.site_id=?""", (site_id,)).fetchall()
    service_by_id = {row["id"]: _service_public(row) for row in services}
    service_ids: dict[str, list[str]] = {}
    for link in links:
        service_ids.setdefault(link["item_id"], []).append(link["service_id"])
    layouts_by_item: dict[str, dict[str, dict[str, int]]] = {}
    for layout in layouts:
        layouts_by_item.setdefault(layout["item_id"], {})[str(layout["breakpoint"])] = {"x": int(layout["grid_x"]), "y": int(layout["grid_y"])}
    item_by_page: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        selected = [service_by_id[service_id] for service_id in service_ids.get(row["id"], []) if service_id in service_by_id]
        item_by_page.setdefault(row["page_id"], []).append({
            "id": row["id"], "pageId": row["page_id"], "name": row["name"], "size": row["size"],
            "iconSource": row["icon_source"], "iconFilename": row["icon_filename"],
            "iconText": row["icon_text"], "iconColor": _normalise_accent_color(row["icon_color"]),
            "faviconServiceId": row["favicon_service_id"] or "", "preferenceRevision": int(row["preference_revision"]),
            "serviceIds": [item["id"] for item in selected], "services": selected,
            "layouts": layouts_by_item.get(row["id"], {}), "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        })
    return {"breakpoints": [16], "pages": [{"id": row["id"], "name": row["name"], "sortOrder": int(row["sort_order"]), "visible": bool(row["visible"]), "items": item_by_page.get(row["id"], [])} for row in pages]}


def get_navigation(user: User) -> dict[str, Any]:
    return _navigation_detail(_owner_site(user)["id"])


def _clean_nav_name(value: Any, label: str = "名称") -> str:
    result = str(value or "").strip()[:120]
    if not result:
        raise ToolboxError("INVALID_NAVIGATION", f"{label}不能为空", status_code=400, tool_id=TOOL_ID)
    return result


def _owned_page(database: sqlite3.Connection, site_id: str, page_id: str) -> sqlite3.Row:
    page = database.execute("SELECT * FROM service_navigator_nav_pages WHERE id=? AND site_id=?", (page_id, site_id)).fetchone()
    if not page:
        raise ToolboxError("NAV_PAGE_NOT_FOUND", "导航页面不存在", status_code=404, tool_id=TOOL_ID)
    return page


def _owned_item(database: sqlite3.Connection, site_id: str, item_id: str) -> sqlite3.Row:
    item = database.execute("""SELECT i.* FROM service_navigator_nav_items i JOIN service_navigator_nav_pages p ON p.id=i.page_id
        WHERE i.id=? AND p.site_id=?""", (item_id, site_id)).fetchone()
    if not item:
        raise ToolboxError("NAV_ITEM_NOT_FOUND", "导航图标不存在", status_code=404, tool_id=TOOL_ID)
    return item


def create_nav_page(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    name, now = _clean_nav_name(payload.get("name"), "页面名称"), now_iso()
    with conn() as database:
        order = int(database.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM service_navigator_nav_pages WHERE site_id=?", (site["id"],)).fetchone()[0])
        page_id = uuid4().hex
        database.execute("INSERT INTO service_navigator_nav_pages(id,site_id,name,sort_order,visible,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (page_id, site["id"], name, order, now, now))
        database.commit()
    return next(page for page in get_navigation(user)["pages"] if page["id"] == page_id)


def update_nav_page(page_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_page(database, site["id"], page_id)
        name = _clean_nav_name(payload.get("name", old["name"]), "页面名称")
        visible = 1 if payload.get("visible", bool(old["visible"])) else 0
        database.execute("UPDATE service_navigator_nav_pages SET name=?,visible=?,updated_at=? WHERE id=?", (name, visible, now_iso(), page_id))
        database.commit()
    return next(page for page in get_navigation(user)["pages"] if page["id"] == page_id)


def delete_nav_page(page_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        _owned_page(database, site["id"], page_id)
        filenames = [row[0] for row in database.execute("SELECT icon_filename FROM service_navigator_nav_items WHERE page_id=? AND icon_filename<>''", (page_id,)).fetchall()]
        database.execute("DELETE FROM service_navigator_nav_pages WHERE id=?", (page_id,))
        database.commit()
    for filename in filenames:
        _remove_navigation_icon(filename)


def reorder_nav_pages(page_ids: list[str], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        existing = [row[0] for row in database.execute("SELECT id FROM service_navigator_nav_pages WHERE site_id=? ORDER BY sort_order,id", (site["id"],)).fetchall()]
        if set(existing) != set(page_ids) or len(existing) != len(page_ids):
            raise ToolboxError("INVALID_NAVIGATION_ORDER", "页面排序数据不完整", status_code=400, tool_id=TOOL_ID)
        for order, page_id in enumerate(page_ids):
            database.execute("UPDATE service_navigator_nav_pages SET sort_order=?,updated_at=? WHERE id=?", (order, now_iso(), page_id))
        database.commit()
    return get_navigation(user)


def _nav_size(value: Any) -> str:
    size = str(value or "small")
    if size not in NAV_SIZES:
        raise ToolboxError("INVALID_NAV_SIZE", "图标尺寸不合法", status_code=400, tool_id=TOOL_ID)
    return size


def _validate_item_services(database: sqlite3.Connection, site_id: str, service_ids: Any) -> tuple[list[str], str]:
    if not isinstance(service_ids, list) or not service_ids:
        raise ToolboxError("INVALID_NAV_SERVICES", "至少关联一个服务", status_code=400, tool_id=TOOL_ID)
    ids = list(dict.fromkeys(str(item) for item in service_ids if str(item)))
    placeholders = ",".join("?" for _ in ids)
    rows = database.execute(f"""SELECT s.id,s.service_type FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id
        WHERE t.site_id=? AND s.id IN ({placeholders})""", [site_id, *ids]).fetchall()
    if len(rows) != len(ids):
        raise ToolboxError("INVALID_NAV_SERVICES", "关联服务不存在或不属于当前站点", status_code=400, tool_id=TOOL_ID)
    kinds = {row["service_type"] for row in rows}
    if len(kinds) != 1:
        raise ToolboxError("MIXED_NAV_SERVICES", "一个图标不能混用 HTTP 服务和端口服务", status_code=400, tool_id=TOOL_ID)
    return ids, next(iter(kinds))


def _clean_icon_source(value: Any, favicon_service_id: str, service_ids: list[str]) -> str:
    source = str(value or "none")
    if source not in {"none", "favicon", "custom", "text"}:
        raise ToolboxError("INVALID_NAV_ICON", "图标来源不合法", status_code=400, tool_id=TOOL_ID)
    if source == "favicon" and favicon_service_id not in service_ids:
        raise ToolboxError("INVALID_NAV_ICON", "favicon 必须来自已关联服务", status_code=400, tool_id=TOOL_ID)
    return source


def _first_available_layout(database: sqlite3.Connection, page_id: str, breakpoint: int, size: str) -> tuple[int, int]:
    width, height = NAV_SIZES[size]
    rows = database.execute("""SELECT i.size,l.grid_x,l.grid_y FROM service_navigator_nav_item_layouts l
        JOIN service_navigator_nav_items i ON i.id=l.item_id WHERE i.page_id=? AND l.breakpoint=?""", (page_id, breakpoint)).fetchall()
    occupied: set[tuple[int, int]] = set()
    for row in rows:
        old_width, old_height = NAV_SIZES[row["size"]]
        occupied.update((x, y) for x in range(row["grid_x"], row["grid_x"] + old_width) for y in range(row["grid_y"], row["grid_y"] + old_height))
    for y in range(0, 1000):
        for x in range(0, breakpoint - width + 1):
            if all((cell_x, cell_y) not in occupied for cell_x in range(x, x + width) for cell_y in range(y, y + height)):
                return x, y
    raise ToolboxError("NAV_LAYOUT_FULL", "导航布局已满", status_code=400, tool_id=TOOL_ID)


def create_nav_item(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        page = _owned_page(database, site["id"], str(payload.get("pageId") or ""))
        service_ids, _kind = _validate_item_services(database, site["id"], payload.get("serviceIds"))
        size = _nav_size(payload.get("size"))
        favicon_service_id = str(payload.get("faviconServiceId") or "")
        source = _clean_icon_source(payload.get("iconSource"), favicon_service_id, service_ids)
        item_id, now = uuid4().hex, now_iso()
        database.execute("INSERT INTO service_navigator_nav_items(id,page_id,name,size,icon_source,icon_text,icon_color,favicon_service_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (item_id, page["id"], _clean_nav_name(payload.get("name"), "图标名称"), size, source, str(payload.get("iconText") or "")[:4], _normalise_accent_color(payload.get("iconColor")), favicon_service_id or None, now, now))
        database.executemany("INSERT INTO service_navigator_nav_item_services(item_id,service_id) VALUES(?,?)", [(item_id, service_id) for service_id in service_ids])
        x, y = _first_available_layout(database, page["id"], 16, size)
        database.execute("INSERT INTO service_navigator_nav_item_layouts(item_id,breakpoint,grid_x,grid_y) VALUES(?,?,?,?)", (item_id, 16, x, y))
        database.commit()
    return _navigation_item_for_user(item_id, user)


def _navigation_item_for_user(item_id: str, user: User) -> dict[str, Any]:
    for page in get_navigation(user)["pages"]:
        for item in page["items"]:
            if item["id"] == item_id:
                return item
    raise ToolboxError("NAV_ITEM_NOT_FOUND", "导航图标不存在", status_code=404, tool_id=TOOL_ID)


def update_nav_item(item_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        item = _owned_item(database, site["id"], item_id)
        existing_ids = [row[0] for row in database.execute("SELECT service_id FROM service_navigator_nav_item_services WHERE item_id=?", (item_id,)).fetchall()]
        service_ids = existing_ids
        changed_services = False
        if "serviceIds" in payload:
            service_ids, _kind = _validate_item_services(database, site["id"], payload["serviceIds"])
            changed_services = set(service_ids) != set(existing_ids)
        else:
            _validate_item_services(database, site["id"], service_ids)
        size = _nav_size(payload.get("size", item["size"]))
        favicon_service_id = str(payload.get("faviconServiceId", item["favicon_service_id"] or "") or "")
        source = _clean_icon_source(payload.get("iconSource", item["icon_source"]), favicon_service_id, service_ids)
        revision = int(item["preference_revision"]) + (1 if changed_services else 0)
        database.execute("UPDATE service_navigator_nav_items SET name=?,size=?,icon_source=?,icon_text=?,icon_color=?,favicon_service_id=?,preference_revision=?,updated_at=? WHERE id=?", (_clean_nav_name(payload.get("name", item["name"]), "图标名称"), size, source, str(payload.get("iconText", item["icon_text"]) or "")[:4], _normalise_accent_color(payload.get("iconColor", item["icon_color"])), favicon_service_id or None, revision, now_iso(), item_id))
        if changed_services:
            database.execute("DELETE FROM service_navigator_nav_item_services WHERE item_id=?", (item_id,))
            database.executemany("INSERT INTO service_navigator_nav_item_services(item_id,service_id) VALUES(?,?)", [(item_id, service_id) for service_id in service_ids])
        database.commit()
    return _navigation_item_for_user(item_id, user)


def delete_nav_item(item_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        item = _owned_item(database, site["id"], item_id)
        database.execute("DELETE FROM service_navigator_nav_items WHERE id=?", (item_id,))
        database.commit()
    _remove_navigation_icon(item["icon_filename"])


def save_nav_layout(page_id: str, breakpoint: int, placements: list[dict[str, Any]], user: User) -> dict[str, Any]:
    if breakpoint != 16:
        raise ToolboxError("INVALID_NAV_BREAKPOINT", "导航布局仅需编辑 16 列，其他档位会自动生成", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        _owned_page(database, site["id"], page_id)
        items = {row["id"]: row for row in database.execute("SELECT * FROM service_navigator_nav_items WHERE page_id=?", (page_id,)).fetchall()}
        placement_by_id = {str(item.get("itemId") or ""): item for item in placements if isinstance(item, dict)}
        if set(placement_by_id) != set(items) or len(placements) != len(items):
            raise ToolboxError("INVALID_NAV_LAYOUT", "布局必须包含页面内所有图标", status_code=400, tool_id=TOOL_ID)
        occupied: set[tuple[int, int]] = set()
        validated: list[tuple[str, int, int]] = []
        for item_id, item in items.items():
            try:
                x, y = int(placement_by_id[item_id].get("x")), int(placement_by_id[item_id].get("y"))
            except (TypeError, ValueError):
                raise ToolboxError("INVALID_NAV_LAYOUT", "布局坐标必须为整数", status_code=400, tool_id=TOOL_ID) from None
            width, height = NAV_SIZES[item["size"]]
            if x < 0 or y < 0 or x + width > breakpoint or y > 1000:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标超出当前网格范围", status_code=400, tool_id=TOOL_ID)
            cells = {(column, row) for column in range(x, x + width) for row in range(y, y + height)}
            if occupied & cells:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标不能重叠", status_code=400, tool_id=TOOL_ID)
            occupied |= cells
            validated.append((item_id, x, y))
        for item_id, x, y in validated:
            database.execute("INSERT INTO service_navigator_nav_item_layouts(item_id,breakpoint,grid_x,grid_y) VALUES(?,?,?,?) ON CONFLICT(item_id,breakpoint) DO UPDATE SET grid_x=excluded.grid_x,grid_y=excluded.grid_y", (item_id, breakpoint, x, y))
        database.commit()
    return get_navigation(user)


def revoke_nav_default(item_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        _owned_item(database, site["id"], item_id)
        database.execute("UPDATE service_navigator_nav_items SET preference_revision=preference_revision+1,updated_at=? WHERE id=?", (now_iso(), item_id))
        database.commit()
    return _navigation_item_for_user(item_id, user)


def _remove_navigation_icon(filename: str) -> None:
    if not filename:
        return
    path = (navigation_icon_dir() / Path(filename).name).resolve()
    if path.parent == navigation_icon_dir().resolve():
        path.unlink(missing_ok=True)


def update_nav_custom_icon(item_id: str, filename: str, content: bytes, user: User) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in NAV_ICON_SUFFIXES:
        raise ToolboxError("INVALID_NAV_ICON", "图标仅支持 PNG、JPEG、WebP 或 ICO", status_code=400, tool_id=TOOL_ID)
    if not content or len(content) > NAV_ICON_LIMIT:
        raise ToolboxError("INVALID_NAV_ICON", "图标不能超过 1MB", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        item = _owned_item(database, site["id"], item_id)
        saved = f"{item_id}-{uuid4().hex[:8]}{suffix}"
        (navigation_icon_dir() / saved).write_bytes(content)
        database.execute("UPDATE service_navigator_nav_items SET icon_source='custom',icon_filename=?,updated_at=? WHERE id=?", (saved, now_iso(), item_id))
        database.commit()
    _remove_navigation_icon(item["icon_filename"])
    return _navigation_item_for_user(item_id, user)


def clear_nav_custom_icon(item_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        item = _owned_item(database, site["id"], item_id)
        database.execute("UPDATE service_navigator_nav_items SET icon_source='none',icon_filename='',updated_at=? WHERE id=?", (now_iso(), item_id))
        database.commit()
    _remove_navigation_icon(item["icon_filename"])
    return _navigation_item_for_user(item_id, user)


def update_background_source(source: str, user: User) -> dict[str, Any]:
    if source not in {"default", "custom", "bing"}:
        raise ToolboxError("INVALID_BACKGROUND", "背景来源不合法", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        current = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()
        if source == "custom" and not current["background_filename"]:
            raise ToolboxError("BACKGROUND_REQUIRED", "请先上传背景图片", status_code=400, tool_id=TOOL_ID)
        database.execute("UPDATE service_navigator_sites SET background_source=?,updated_at=? WHERE id=?", (source, now_iso(), site["id"]))
        database.commit()
    return _site_public(_owner_site(user))


def set_all_services_visible(visible: bool, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        database.execute("UPDATE service_navigator_sites SET show_all_services=?,updated_at=? WHERE id=?", (1 if visible else 0, now_iso(), site["id"]))
        database.commit()
    return _site_public(_owner_site(user))


def update_custom_background(filename: str, content: bytes, user: User) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in BACKGROUND_SUFFIXES:
        raise ToolboxError("INVALID_BACKGROUND", "背景仅支持 PNG、JPEG 或 WebP", status_code=400, tool_id=TOOL_ID)
    if not content or len(content) > BACKGROUND_LIMIT:
        raise ToolboxError("INVALID_BACKGROUND", "背景图片不能超过 6MB", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    saved = f"{site['id']}-{uuid4().hex[:8]}{suffix}"
    (background_dir() / saved).write_bytes(content)
    with conn() as database:
        old = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()["background_filename"]
        database.execute("UPDATE service_navigator_sites SET background_source='custom',background_filename=?,updated_at=? WHERE id=?", (saved, now_iso(), site["id"]))
        database.commit()
    _remove_background(old)
    return _site_public(_owner_site(user))


def _remove_background(filename: str) -> None:
    if not filename:
        return
    path = (background_dir() / Path(filename).name).resolve()
    if path.parent == background_dir().resolve():
        path.unlink(missing_ok=True)


def _cleanup_empty_navigation_items(database: sqlite3.Connection, site_id: str) -> list[str]:
    """Remove icons that became meaningless after all linked services vanished."""
    rows = database.execute("""SELECT i.id,i.icon_filename FROM service_navigator_nav_items i
        JOIN service_navigator_nav_pages p ON p.id=i.page_id WHERE p.site_id=?
        AND NOT EXISTS (SELECT 1 FROM service_navigator_nav_item_services links WHERE links.item_id=i.id)""", (site_id,)).fetchall()
    if rows:
        database.executemany("DELETE FROM service_navigator_nav_items WHERE id=?", [(row["id"],) for row in rows])
    return [row["icon_filename"] for row in rows if row["icon_filename"]]


def add_target(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    label = str(payload.get("label") or "").strip()[:100]
    address = normalize_address(str(payload.get("address") or ""))
    if not label:
        label = address
    custom_ports = normalize_ports(str(payload.get("customPorts") or ""))
    with conn() as database:
        count = database.execute("SELECT COUNT(*) FROM service_navigator_targets WHERE site_id=?", (site["id"],)).fetchone()[0]
        if count >= MAX_TARGETS:
            raise ToolboxError("TARGET_LIMIT", f"每个站点最多配置 {MAX_TARGETS} 个目标", status_code=400, tool_id=TOOL_ID)
        now = now_iso()
        target_id = uuid4().hex
        try:
            database.execute("INSERT INTO service_navigator_targets(id,site_id,label,address,custom_ports,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (target_id, site["id"], label, address, custom_ports, now, now))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("TARGET_EXISTS", "该目标已存在", status_code=409, tool_id=TOOL_ID) from exc
        row = database.execute("SELECT * FROM service_navigator_targets WHERE id=?", (target_id,)).fetchone()
    return _target_public(row)


def _owned_target(database: sqlite3.Connection, site_id: str, target_id: str) -> sqlite3.Row:
    row = database.execute("SELECT * FROM service_navigator_targets WHERE id=? AND site_id=?", (target_id, site_id)).fetchone()
    if row is None:
        raise ToolboxError("TARGET_NOT_FOUND", "目标不存在", status_code=404, tool_id=TOOL_ID)
    return row


def update_target(target_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_target(database, site["id"], target_id)
        label = str(payload.get("label", old["label"]) or "").strip()[:100]
        address = normalize_address(str(payload.get("address", old["address"]) or ""))
        if not label:
            label = address
        ports = normalize_ports(str(payload.get("customPorts", old["custom_ports"]) or ""))
        show_in_navigation = 1 if payload.get("showInNavigation", bool(old["show_in_navigation"])) else 0
        try:
            database.execute("UPDATE service_navigator_targets SET label=?,address=?,custom_ports=?,show_in_navigation=?,updated_at=? WHERE id=?", (label, address, ports, show_in_navigation, now_iso(), target_id))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("TARGET_EXISTS", "该目标已存在", status_code=409, tool_id=TOOL_ID) from exc
        row = database.execute("SELECT * FROM service_navigator_targets WHERE id=?", (target_id,)).fetchone()
    return _target_public(row)


def delete_target(target_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        _owned_target(database, site["id"], target_id)
        filenames = [row[0] for row in database.execute("SELECT favicon_filename FROM service_navigator_services WHERE target_id=?", (target_id,)).fetchall()]
        database.execute("DELETE FROM service_navigator_targets WHERE id=?", (target_id,))
        navigation_filenames = _cleanup_empty_navigation_items(database, site["id"])
        database.commit()
    for filename in filenames:
        _remove_icon(filename)
    for filename in navigation_filenames:
        _remove_navigation_icon(filename)


def list_targets(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site["id"],)).fetchall()
    return [_target_public(row) for row in rows]


def list_services(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.display_name,s.port,s.id", (site["id"],)).fetchall()
    return [_service_public(row) for row in rows]


def update_service(service_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
        if row is None:
            raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
        service_type = str(payload.get("serviceType", row["service_type"]) or "port").strip().lower()
        if service_type not in {"http", "port"}:
            raise ToolboxError("INVALID_SERVICE_TYPE", "服务类型必须是 HTTP 服务或端口服务", status_code=400, tool_id=TOOL_ID)
        if service_type != row["service_type"]:
            conflicting = database.execute("""SELECT 1 FROM service_navigator_nav_item_services links
                JOIN service_navigator_nav_item_services peers ON peers.item_id=links.item_id AND peers.service_id<>links.service_id
                JOIN service_navigator_services peer_service ON peer_service.id=peers.service_id
                WHERE links.service_id=? AND peer_service.service_type<>? LIMIT 1""", (service_id, service_type)).fetchone()
            if conflicting:
                raise ToolboxError("NAV_SERVICE_TYPE_CONFLICT", "该服务已与其他类型服务关联到同一图标；请先调整导航图标", status_code=409, tool_id=TOOL_ID)
        values = {
            "display_name": str(payload.get("displayName", row["display_name"]) or "").strip()[:120],
            "description": str(payload.get("description", row["description"]) or "").strip()[:500],
            "navigation_url": _clean_navigation_url(str(payload.get("navigationUrl", row["navigation_url"]) or "")),
            "connection_command": str(payload.get("connectionCommand", row["connection_command"]) or "").strip()[:500],
            "service_type": service_type,
            "health_enabled": 1 if payload.get("healthEnabled", bool(row["health_enabled"])) else 0,
            "health_url": _clean_health_url(str(payload.get("healthUrl", row["health_url"]) or "")),
            "updated_at": now_iso(),
            "id": service_id,
        }
        if service_type == "port":
            values["navigation_url"] = ""
            values["health_enabled"] = 0
            values["health_url"] = ""
        if service_type != row["service_type"] or values["navigation_url"] != row["navigation_url"] or values["connection_command"] != row["connection_command"]:
            database.execute("""UPDATE service_navigator_nav_items SET preference_revision=preference_revision+1,updated_at=?
                WHERE id IN (SELECT item_id FROM service_navigator_nav_item_services WHERE service_id=?)""", (values["updated_at"], service_id))
        database.execute("""UPDATE service_navigator_services SET display_name=:display_name,description=:description,navigation_url=:navigation_url,connection_command=:connection_command,service_type=:service_type,health_enabled=:health_enabled,health_url=:health_url,updated_at=:updated_at WHERE id=:id""", values)
        database.commit()
        updated = database.execute("SELECT * FROM service_navigator_services WHERE id=?", (service_id,)).fetchone()
    return _service_public(updated)


def _clean_navigation_url(value: str) -> str:
    value = value.strip()[:1000]
    if value and not value.startswith(("http://", "https://")):
        raise ToolboxError("INVALID_NAVIGATION_URL", "导航 URL 必须以 http:// 或 https:// 开头", status_code=400, tool_id=TOOL_ID)
    return value


def _clean_health_url(value: str) -> str:
    """Health URLs are intentionally limited to direct HTTP(S) requests."""
    return _clean_navigation_url(value)


def request_scan(user: User, target_id: str | None = None) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        if target_id:
            _owned_target(database, site["id"], target_id)
        exists = database.execute("SELECT 1 FROM service_navigator_scan_runs WHERE site_id=? AND status IN ('queued','running')", (site["id"],)).fetchone()
        if exists:
            raise ToolboxError("SCAN_IN_PROGRESS", "该站点已有扫描任务正在进行", status_code=409, tool_id=TOOL_ID)
        run_id = uuid4().hex
        target_count = 1 if target_id else database.execute("SELECT COUNT(*) FROM service_navigator_targets WHERE site_id=?", (site["id"],)).fetchone()[0]
        summary = json.dumps({"targetCount": target_count, "completedTargetCount": 0, "successCount": 0}, ensure_ascii=False)
        database.execute("INSERT INTO service_navigator_scan_runs(id,site_id,target_id,status,requested_at,summary_json) VALUES(?,?,?,'queued',?,?)", (run_id, site["id"], target_id, now_iso(), summary))
        database.commit()
    return {"id": run_id, "status": "queued"}


def get_scan(run_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_scan_runs WHERE id=? AND site_id=?", (run_id, site["id"])).fetchone()
    if row is None:
        raise ToolboxError("SCAN_NOT_FOUND", "扫描任务不存在", status_code=404, tool_id=TOOL_ID)
    return _run_public(row)


def run_scan(run_id: str) -> None:
    with ACTIVE_LOCK:
        if run_id in ACTIVE_RUNS:
            return
        ACTIVE_RUNS.add(run_id)
    try:
        with conn() as database:
            run = database.execute("SELECT * FROM service_navigator_scan_runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] != "queued":
                return
            database.execute("UPDATE service_navigator_scan_runs SET status='running',started_at=? WHERE id=?", (now_iso(), run_id))
            targets = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=?" + (" AND id=?" if run["target_id"] else "") + " ORDER BY label,address", ((run["site_id"], run["target_id"]) if run["target_id"] else (run["site_id"],))).fetchall()
            database.commit()
        if not targets:
            _finish_run(run_id, "failed", "没有可扫描的目标", {"targetCount": 0, "completedTargetCount": 0, "successCount": 0})
            return
        results: list[dict[str, Any]] = []
        _update_run_progress(run_id, completed=0, total=len(targets), successes=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_scan_target, dict(target)) for target in targets]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # defensive: individual failures should not discard other targets
                    results.append({"ok": False, "error": str(exc)[:500], "targetId": ""})
                _update_run_progress(run_id, completed=len(results), total=len(targets), successes=sum(1 for item in results if item.get("ok")))
        successes = sum(1 for item in results if item.get("ok"))
        status = "success" if successes == len(results) else ("partial" if successes else "failed")
        error = "" if status == "success" else "部分目标扫描失败" if status == "partial" else "所有目标扫描失败"
        _finish_run(run_id, status, error, {"targets": results, "successCount": successes, "targetCount": len(results), "completedTargetCount": len(results)})
    except Exception as exc:  # noqa: BLE001
        _finish_run(run_id, "failed", str(exc)[:500], {})
    finally:
        with ACTIVE_LOCK:
            ACTIVE_RUNS.discard(run_id)


def _finish_run(run_id: str, status: str, error: str, summary: dict[str, Any]) -> None:
    with conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET status=?,finished_at=?,error=?,summary_json=? WHERE id=?", (status, now_iso(), error, json.dumps(summary, ensure_ascii=False), run_id))
        database.commit()


def _update_run_progress(run_id: str, *, completed: int, total: int, successes: int) -> None:
    summary = {"targetCount": total, "completedTargetCount": completed, "successCount": successes}
    with conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET summary_json=? WHERE id=? AND status='running'", (json.dumps(summary, ensure_ascii=False), run_id))
        database.commit()


def _scan_target(target: dict[str, Any]) -> dict[str, Any]:
    target_id, address = target["id"], target["address"]
    try:
        addresses = resolve_addresses(address)
    except OSError as exc:
        return {"targetId": target_id, "address": address, "ok": False, "error": f"域名解析失败：{exc}"}

    discovered: dict[int, dict[str, Any]] = {}
    phase_errors: list[str] = []
    ports = ports_for_target(target.get("custom_ports", ""))
    # This semaphore covers the complete target operation (not just a child
    # process), so separate site scans cannot exceed three active targets.
    with SCAN_SEMAPHORE:
        for concrete_address in addresses:
            try:
                for item in _scan_address(concrete_address, ports):
                    port = int(item["port"])
                    current = discovered.get(port)
                    resolved = set(current.get("resolvedAddresses", set())) if current else set()
                    if current is None or _fingerprint_score(item) > _fingerprint_score(current):
                        discovered[port] = item
                    resolved.add(concrete_address)
                    discovered[port]["resolvedAddresses"] = resolved
            except ToolboxError as exc:
                phase_errors.append(exc.message)
    if phase_errors:
        return {"targetId": target_id, "address": address, "ok": False, "error": "; ".join(phase_errors)[:500], "openPorts": sorted(discovered)}
    services: list[dict[str, Any]] = []
    for port, item in discovered.items():
        item["resolvedAddresses"] = sorted(item.get("resolvedAddresses", set()))
        item.update(_enrich_web_service(address, item))
        services.append(item)
    _persist_target_scan(target, services)
    return {"targetId": target_id, "address": address, "ok": True, "openPorts": sorted(discovered), "serviceCount": len(services)}


def resolve_addresses(address: str) -> list[str]:
    try:
        return [str(ipaddress.ip_address(address))]
    except ValueError:
        resolved: list[str] = []
        for result in socket.getaddrinfo(address, None, type=socket.SOCK_STREAM):
            candidate = result[4][0]
            if candidate not in resolved:
                resolved.append(candidate)
            if len(resolved) >= MAX_DNS_ADDRESSES:
                break
        if not resolved:
            raise OSError("未找到 A 或 AAAA 记录")
        return resolved


def ports_for_target(custom_ports: str) -> tuple[int, ...]:
    """Return the built-in TCP set plus validated, persisted custom ports."""
    custom: set[int] = set()
    for value in custom_ports.split(","):
        if not value:
            continue
        if "-" in value:
            start, end = (int(part) for part in value.split("-", 1))
            custom.update(range(start, end + 1))
        else:
            custom.add(int(value))
    return tuple(sorted(set(COMMON_TCP_PORTS).union(custom)))


def _scan_address(address: str, ports: tuple[int, ...]) -> list[dict[str, Any]]:
    """Connect-scan one concrete IP without executing external programs."""
    deadline = time.monotonic() + HOST_SCAN_TIMEOUT
    discovered: list[dict[str, Any]] = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=PORT_SCAN_WORKERS)
    iterator = iter(ports)
    futures: set[concurrent.futures.Future[dict[str, Any] | None]] = set()

    def submit_next() -> bool:
        try:
            futures.add(executor.submit(_scan_open_port, address, next(iterator)))
            return True
        except StopIteration:
            return False

    for _ in range(PORT_SCAN_WORKERS):
        if not submit_next():
            break
    try:
        while futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise concurrent.futures.TimeoutError
            done, _ = concurrent.futures.wait(futures, timeout=remaining, return_when=concurrent.futures.FIRST_COMPLETED)
            if not done:
                raise concurrent.futures.TimeoutError
            for future in done:
                futures.remove(future)
                item = future.result()
                if item:
                    discovered.append(item)
                submit_next()
    except concurrent.futures.TimeoutError as exc:
        raise ToolboxError("SCAN_TIMEOUT", "目标端口扫描超时", status_code=504, tool_id=TOOL_ID) from exc
    finally:
        # A socket operation has its own short timeout.  Do not block a task
        # past the host deadline waiting for any straggling worker.
        executor.shutdown(wait=False, cancel_futures=True)
    return discovered


def _scan_open_port(address: str, port: int) -> dict[str, Any] | None:
    if not _tcp_connects(address, port):
        return None
    return _identify_service(address, port)


def _tcp_connects(address: str, port: int) -> bool:
    for attempt in range(2):
        try:
            with socket.create_connection((address, port), timeout=PORT_CONNECT_TIMEOUT):
                return True
        except ConnectionRefusedError:
            return False
        except (socket.timeout, TimeoutError):
            if attempt == 0:
                continue
            return False
        except OSError:
            return False
    return False


def _identify_service(address: str, port: int) -> dict[str, Any]:
    hint = PORT_SERVICE_HINTS.get(port, "unknown")
    item = {
        "port": port,
        "protocol": "tcp",
        "serviceName": hint,
        "product": "",
        "version": "",
        "extraInfo": "",
        "tunnel": "ssl" if hint == "https" else "",
    }
    try:
        with socket.create_connection((address, port), timeout=PORT_PROBE_TIMEOUT) as connection:
            connection.settimeout(PORT_PROBE_TIMEOUT)
            banner = connection.recv(512)
    except (OSError, socket.timeout):
        return item
    return _apply_banner_fingerprint(item, banner)


def _apply_banner_fingerprint(item: dict[str, Any], banner: bytes) -> dict[str, Any]:
    text = banner.decode("utf-8", errors="replace").replace("\x00", " ").strip()
    port = int(item["port"])
    if text.startswith("SSH-"):
        item["serviceName"] = "ssh"
        identity = text.split(None, 1)[0]
        match = re.search(r"(?:OpenSSH|dropbear)[_-]?([^\s]+)?", identity, re.IGNORECASE)
        item["product"] = "OpenSSH" if "openssh" in identity.lower() else "Dropbear" if "dropbear" in identity.lower() else "SSH"
        item["version"] = (match.group(1) or "")[:80] if match else ""
    elif banner[:1] == b"\x0a" and port == 3306:
        item["serviceName"] = "mysql"
        item["product"] = "MySQL"
        item["version"] = banner[1:].split(b"\x00", 1)[0].decode("ascii", errors="replace")[:80]
    elif text.startswith("HTTP/"):
        item["serviceName"] = "http"
        item["product"] = "HTTP"
    elif text.startswith("+PONG"):
        item["serviceName"] = "redis"
        item["product"] = "Redis"
    elif text.startswith("220"):
        if port == 21:
            item["serviceName"] = "ftp"
        elif port in {25, 465, 587}:
            item["serviceName"] = "smtp"
        item["extraInfo"] = re.sub(r"\s+", " ", text)[:160]
    return item


def _fingerprint_score(item: dict[str, Any]) -> int:
    return sum(bool(item.get(key)) for key in ("serviceName", "product", "version", "extraInfo"))


def _is_web(item: dict[str, Any]) -> bool:
    name = str(item.get("serviceName") or "").lower()
    return "http" in name or int(item.get("port") or 0) in WEB_PORTS


def _url_for(address: str, port: int, scheme: str) -> str:
    host = f"[{address}]" if ":" in address and not address.startswith("[") else address
    return f"{scheme}://{host}:{port}/"


def _enrich_web_service(address: str, item: dict[str, Any]) -> dict[str, Any]:
    if not _is_web(item):
        return {"httpTitle": "", "detectedUrl": "", "faviconFilename": ""}
    name = str(item.get("serviceName") or "").lower()
    first = "https" if item.get("tunnel") == "ssl" or "https" in name or int(item["port"]) in {443, 444, 8443} else "http"
    schemes = [first, "http" if first == "https" else "https"]
    for scheme in schemes:
        url = _url_for(address, int(item["port"]), scheme)
        metadata = fetch_web_metadata(url)
        if metadata:
            return {"httpTitle": metadata["title"], "detectedUrl": metadata["url"], "faviconFilename": metadata["faviconFilename"]}
    return {"httpTitle": "", "detectedUrl": _url_for(address, int(item["port"]), first), "faviconFilename": ""}


def fetch_web_metadata(url: str) -> dict[str, str] | None:
    try:
        response = _open_web_url(url)
        with response:
            if not 200 <= response.getcode() < 300:
                return None
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None
            content = _read_limited(response, HTTP_METADATA_LIMIT)
            if content is None:
                return None
            text = content.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            final_url = response.geturl()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()[:160] if title_match else ""
        icon_match = re.search(r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"']", text, re.IGNORECASE)
        icon_url = urljoin(final_url, icon_match.group(1)) if icon_match else urljoin(final_url, "/favicon.ico")
        filename = _download_favicon(icon_url)
        return {"title": title, "url": final_url, "faviconFilename": filename}
    except (HTTPError, URLError, OSError, ValueError, ssl.SSLError):
        return None


class _SafeRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urlparse(newurl).scheme not in {"http", "https"}:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_web_url(url: str):  # type: ignore[no-untyped-def]
    request = Request(url, headers={"User-Agent": "Pansis-Service-Navigator/1.0"})
    # The probe collects only public page metadata.  It must tolerate the
    # self-signed certificates common on private service dashboards.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = build_opener(_SafeRedirectHandler(), HTTPSHandler(context=context))
    return opener.open(request, timeout=3.0)


def _download_favicon(url: str) -> str:
    try:
        with _open_web_url(url) as response:
            if not 200 <= response.getcode() < 300:
                return ""
            content = _read_limited(response, FAVICON_LIMIT)
            content_type = response.headers.get_content_type().lower()
        allowed = {"image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        if content is None or content_type not in allowed:
            return ""
        filename = f"{uuid4().hex}{allowed[content_type]}"
        (icon_dir() / filename).write_bytes(content)
        return filename
    except (HTTPError, URLError, OSError, ValueError, ssl.SSLError):
        return ""


def _read_limited(response: Any, limit: int) -> bytes | None:
    content = response.read(limit + 1)
    return content if len(content) <= limit else None


def _remove_icon(filename: str) -> None:
    if not filename:
        return
    path = (icon_dir() / Path(filename).name).resolve()
    if path.parent == icon_dir().resolve():
        path.unlink(missing_ok=True)


def _persist_target_scan(target: dict[str, Any], services: list[dict[str, Any]]) -> None:
    now = now_iso()
    old_icons: list[str] = []
    with conn() as database:
        existing = {int(row["port"]): row for row in database.execute("SELECT * FROM service_navigator_services WHERE target_id=? AND protocol='tcp'", (target["id"],)).fetchall()}
        seen: set[int] = set()
        for detected in services:
            port = int(detected["port"])
            seen.add(port)
            old = existing.get(port)
            if old:
                old_icon = old["favicon_filename"]
                new_icon = detected.get("faviconFilename") or old_icon
                if detected.get("faviconFilename") and old_icon and old_icon != new_icon:
                    old_icons.append(old_icon)
                database.execute("""UPDATE service_navigator_services SET state='online',service_name=?,product=?,version=?,extra_info=?,resolved_addresses_json=?,http_title=?,favicon_filename=?,detected_url=?,last_seen_at=?,updated_at=? WHERE id=?""", (detected.get("serviceName", "unknown"), detected.get("product", ""), detected.get("version", ""), detected.get("extraInfo", ""), json.dumps(detected.get("resolvedAddresses", [])), detected.get("httpTitle", ""), new_icon, detected.get("detectedUrl", ""), now, now, old["id"]))
            else:
                service_name = str(detected.get("serviceName") or "unknown")
                host = target["address"]
                command = default_command(service_name, host, port)
                health_enabled = 1 if _is_web(detected) else 0
                service_type = "http" if _is_web(detected) else "port"
                database.execute("""INSERT INTO service_navigator_services(id,target_id,protocol,port,state,service_name,product,version,extra_info,resolved_addresses_json,http_title,favicon_filename,detected_url,connection_command,service_type,visible,health_enabled,first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (uuid4().hex, target["id"], "tcp", port, "online", service_name, detected.get("product", ""), detected.get("version", ""), detected.get("extraInfo", ""), json.dumps(detected.get("resolvedAddresses", [])), detected.get("httpTitle", ""), detected.get("faviconFilename", ""), detected.get("detectedUrl", ""), command, service_type, 1, health_enabled, now, now, now))
        for port, old in existing.items():
            if port not in seen:
                database.execute("UPDATE service_navigator_services SET state='offline',updated_at=? WHERE id=?", (now, old["id"]))
        database.commit()
    for filename in old_icons:
        _remove_icon(filename)


def default_command(service_name: str, host: str, port: int) -> str:
    normalized = service_name.lower()
    if normalized in {"postgres", "postgresql"}:
        normalized = "postgresql"
    elif normalized == "redis-server":
        normalized = "redis"
    template = COMMAND_TEMPLATES.get(normalized)
    return template.format(host=host, port=port) if template else f"{host}:{port}"


# ---------------------------------------------------------------------------
# HTTP health checks and alerting
# ---------------------------------------------------------------------------

def _health_settings_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "checkIntervalSeconds": int(row["check_interval_seconds"]),
        "emailRecipients": json.loads(row["email_recipients_json"] or "[]"),
        "confirmCount": int(row["confirm_count"]),
        "repeatIntervalSeconds": int(row["repeat_interval_seconds"]),
        "maxRepeatCount": int(row["max_repeat_count"]),
        "lastCheckedAt": row["last_checked_at"],
        "updatedAt": row["updated_at"],
    }


def _ensure_health_settings(database: sqlite3.Connection, site_id: str) -> sqlite3.Row:
    database.execute(
        "INSERT OR IGNORE INTO service_navigator_health_settings(site_id,updated_at) VALUES(?,?)",
        (site_id, now_iso()),
    )
    row = database.execute("SELECT * FROM service_navigator_health_settings WHERE site_id=?", (site_id,)).fetchone()
    assert row is not None
    return row


def _clean_recipients(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    recipients: list[str] = []
    for candidate in raw:
        email = str(candidate).strip()
        if not email:
            continue
        if len(email) > 254 or not re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", email):
            raise ToolboxError("INVALID_HEALTH_RECIPIENT", "收件人邮箱格式不合法", status_code=400, tool_id=TOOL_ID)
        if email.lower() not in {item.lower() for item in recipients}:
            recipients.append(email)
    return recipients[:100]


def get_health_settings(user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = _ensure_health_settings(database, site["id"])
        database.commit()
    return _health_settings_public(row)


def update_health_settings(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _ensure_health_settings(database, site["id"])
        interval = int(payload.get("checkIntervalSeconds", old["check_interval_seconds"]) or 0)
        confirm = int(payload.get("confirmCount", old["confirm_count"]) or 0)
        repeat = int(payload.get("repeatIntervalSeconds", old["repeat_interval_seconds"]) or 0)
        maximum = int(payload.get("maxRepeatCount", old["max_repeat_count"]) or 0)
        if not HEALTH_MIN_INTERVAL <= interval <= HEALTH_MAX_INTERVAL:
            raise ToolboxError("INVALID_HEALTH_INTERVAL", f"检测间隔必须在 {HEALTH_MIN_INTERVAL}–{HEALTH_MAX_INTERVAL} 秒之间", status_code=400, tool_id=TOOL_ID)
        if not 1 <= confirm <= 20:
            raise ToolboxError("INVALID_HEALTH_CONFIRM", "连续失败确认次数必须在 1–20 之间", status_code=400, tool_id=TOOL_ID)
        if repeat < 0 or repeat > HEALTH_MAX_INTERVAL * 30:
            raise ToolboxError("INVALID_HEALTH_REPEAT", "重复告警冷却时间不合法", status_code=400, tool_id=TOOL_ID)
        if maximum < 0 or maximum > 100000:
            raise ToolboxError("INVALID_HEALTH_REPEAT_MAX", "最大重复告警次数不合法", status_code=400, tool_id=TOOL_ID)
        recipients = _clean_recipients(payload.get("emailRecipients", json.loads(old["email_recipients_json"] or "[]")))
        database.execute("""UPDATE service_navigator_health_settings
            SET check_interval_seconds=?,email_recipients_json=?,confirm_count=?,repeat_interval_seconds=?,max_repeat_count=?,updated_at=?
            WHERE site_id=?""", (interval, json.dumps(recipients, ensure_ascii=False), confirm, repeat, maximum, now_iso(), site["id"]))
        database.commit()
        row = database.execute("SELECT * FROM service_navigator_health_settings WHERE site_id=?", (site["id"],)).fetchone()
    return _health_settings_public(row)


def _health_url(service_row: sqlite3.Row | dict[str, Any]) -> str:
    item = _row(service_row) if isinstance(service_row, sqlite3.Row) else service_row
    return str(item.get("health_url") or item.get("navigation_url") or item.get("detected_url") or "")


def _check_http_health(url: str) -> dict[str, Any]:
    """Perform a small direct GET request; body data is never retained."""
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "Pansis-Service-Navigator-Health/1.0", "Accept": "*/*"})
    try:
        # Health checks deliberately validate TLS. A broken certificate is an
        # outage for a browser-facing HTTPS endpoint and is reported as such.
        opener = build_opener(_SafeRedirectHandler(), HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=HEALTH_TIMEOUT) as response:
            status_code = int(response.getcode())
            final_url = str(response.geturl())[:1000]
        latency = max(0, round((time.monotonic() - started) * 1000))
        return {"status": "healthy" if 200 <= status_code < 400 else "unhealthy", "statusCode": status_code, "latencyMs": latency, "finalUrl": final_url, "error": "" if 200 <= status_code < 400 else f"HTTP {status_code}"}
    except HTTPError as exc:
        latency = max(0, round((time.monotonic() - started) * 1000))
        healthy = 200 <= int(exc.code) < 400
        return {"status": "healthy" if healthy else "unhealthy", "statusCode": int(exc.code), "latencyMs": latency, "finalUrl": str(exc.geturl() or url)[:1000], "error": "" if healthy else f"HTTP {exc.code}"}
    except Exception as exc:  # URL/TLS/socket/timeout errors all mean unhealthy
        latency = max(0, round((time.monotonic() - started) * 1000))
        message = str(exc) or type(exc).__name__
        return {"status": "unhealthy", "statusCode": None, "latencyMs": latency, "finalUrl": url[:1000], "error": message[:500]}


def _event(database: sqlite3.Connection, site_id: str, service_id: str, event_type: str, message: str, details: dict[str, Any]) -> None:
    database.execute("INSERT INTO service_navigator_health_events(id,site_id,service_id,event_type,message,details_json,created_at) VALUES(?,?,?,?,?,?,?)", (uuid4().hex, site_id, service_id, event_type, message[:500], json.dumps(details, ensure_ascii=False), now_iso()))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _alert_email(site: dict[str, Any], service: dict[str, Any], sample: dict[str, Any], recipients: list[str], *, recovery: bool, repeated: bool) -> None:
    name = service.get("display_name") or service.get("http_title") or service.get("service_name") or "HTTP 服务"
    url = sample.get("finalUrl") or _health_url(service)
    timestamp = now_iso().replace("T", " ").replace("+00:00", " UTC")
    if recovery:
        subject = f"【服务导航】{site['title']}：{name} 已恢复"
        body = f"服务已恢复健康。\n\n站点：{site['title']}\n服务：{name}\n健康检查 URL：{url}\n状态码：{sample.get('statusCode') or '—'}\n耗时：{sample.get('latencyMs')} ms\n时间：{timestamp}"
    else:
        prefix = "持续异常提醒" if repeated else "服务异常"
        subject = f"【服务导航】{site['title']}：{name} {prefix}"
        body = f"HTTP 健康检查发现服务异常。\n\n站点：{site['title']}\n服务：{name}\n健康检查 URL：{url}\n失败原因：{sample.get('error') or '健康检查失败'}\n状态码：{sample.get('statusCode') or '—'}\n耗时：{sample.get('latencyMs')} ms\n时间：{timestamp}"
    platform_send_email(recipients, subject, body)


def _record_health_result(site: dict[str, Any], service: dict[str, Any], sample: dict[str, Any], *, manual: bool) -> dict[str, Any]:
    checked_at = now_iso()
    with conn() as database:
        database.execute("""INSERT INTO service_navigator_health_snapshots(id,service_id,checked_at,status,status_code,latency_ms,final_url,error,manual)
            VALUES(?,?,?,?,?,?,?,?,?)""", (uuid4().hex, service["id"], checked_at, sample["status"], sample.get("statusCode"), sample.get("latencyMs"), sample.get("finalUrl", ""), sample.get("error", ""), 1 if manual else 0))
        database.execute("""UPDATE service_navigator_services SET health_status=?,last_health_checked_at=?,last_health_status_code=?,last_health_latency_ms=?,last_health_error=?,updated_at=? WHERE id=?""", (sample["status"], checked_at, sample.get("statusCode"), sample.get("latencyMs"), sample.get("error", ""), checked_at, service["id"]))
        database.execute("UPDATE service_navigator_health_settings SET last_checked_at=? WHERE site_id=?", (checked_at, site["id"]))
        if manual:
            database.commit()
            return sample
        settings = _ensure_health_settings(database, site["id"])
        alert = database.execute("SELECT * FROM service_navigator_health_alert_states WHERE service_id=?", (service["id"],)).fetchone()
        old_failures = int(alert["consecutive_failures"]) if alert else 0
        was_alerting = bool(alert["is_alerting"]) if alert else False
        repeat_count = int(alert["repeat_count"]) if alert else 0
        exhausted = bool(alert["repeat_exhausted"]) if alert else False
        last_alerted = alert["last_alerted_at"] if alert else None
        should_email = False
        recovery = False
        repeated = False
        if sample["status"] == "healthy":
            recovery = was_alerting
            failures, is_alerting, repeat_count, exhausted, last_alerted = 0, False, 0, False, None
        else:
            failures = old_failures + 1
            is_alerting = was_alerting
            if failures >= int(settings["confirm_count"]):
                last_time = _parse_time(last_alerted)
                interval_passed = not last_time or datetime.now(timezone.utc) - last_time >= timedelta(seconds=int(settings["repeat_interval_seconds"]))
                limit_ok = int(settings["max_repeat_count"]) == 0 or repeat_count < int(settings["max_repeat_count"])
                if not was_alerting:
                    should_email, is_alerting, repeated = True, True, False
                elif interval_passed and limit_ok and not exhausted:
                    should_email, repeated = True, True
                if should_email:
                    repeat_count += 1
                    last_alerted = checked_at
                    exhausted = int(settings["max_repeat_count"]) > 0 and repeat_count >= int(settings["max_repeat_count"])
                is_alerting = True
        database.execute("""INSERT INTO service_navigator_health_alert_states(service_id,consecutive_failures,is_alerting,last_alerted_at,repeat_count,repeat_exhausted,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(service_id) DO UPDATE SET consecutive_failures=excluded.consecutive_failures,is_alerting=excluded.is_alerting,last_alerted_at=excluded.last_alerted_at,repeat_count=excluded.repeat_count,repeat_exhausted=excluded.repeat_exhausted,updated_at=excluded.updated_at""", (service["id"], failures, 1 if is_alerting else 0, last_alerted, repeat_count, 1 if exhausted else 0, checked_at))
        if recovery:
            _event(database, site["id"], service["id"], "recovered", "服务已恢复健康", sample)
        elif should_email:
            _event(database, site["id"], service["id"], "alert_triggered" if not repeated else "alert_repeated", "服务健康检查异常", sample)
        database.commit()
        recipients = json.loads(settings["email_recipients_json"] or "[]")
    # SMTP is intentionally outside the SQLite transaction. A mail failure
    # must never lose a health snapshot or block other services.
    if (should_email or recovery) and recipients:
        try:
            _alert_email(site, service, sample, recipients, recovery=recovery, repeated=repeated)
            with conn() as database:
                _event(database, site["id"], service["id"], "recovery_email_sent" if recovery else "alert_email_sent", f"邮件已发送给 {len(recipients)} 个收件人", {"recipients": recipients})
                database.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service navigator health email failed: %s", exc)
            with conn() as database:
                _event(database, site["id"], service["id"], "recovery_email_failed" if recovery else "alert_email_failed", f"邮件发送失败：{str(exc)[:350]}", {"recipients": recipients, "error": str(exc)[:500]})
                database.commit()
    return sample


def _check_service_health(site: dict[str, Any], service: dict[str, Any], *, manual: bool) -> dict[str, Any]:
    url = _health_url(service)
    if not url:
        sample = {"status": "unknown", "statusCode": None, "latencyMs": None, "finalUrl": "", "error": "未配置可用的健康检查 URL"}
        # A web service discovered without a working URL remains unknown rather
        # than producing a false outage alert.
        return _record_health_result(site, service, sample, manual=True)
    with HEALTH_SEMAPHORE:
        sample = _check_http_health(url)
    return _record_health_result(site, service, sample, manual=manual)


def check_health(service_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
    if not row:
        raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
    # The persisted toggle is authoritative. Older discoveries can have a
    # weak/unknown fingerprint even though the owner explicitly enabled HTTP
    # health checks in the editor.
    if row["service_type"] != "http" or not row["health_enabled"]:
        raise ToolboxError("HEALTH_NOT_ENABLED", "该服务未启用 HTTP 健康检测", status_code=400, tool_id=TOOL_ID)
    return _check_service_health(site, _row(row), manual=True)


def check_site_health(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    return _collect_site_health(site, manual=True)


def _collect_site_health(site: dict[str, Any], *, manual: bool) -> list[dict[str, Any]]:
    with conn() as database:
        rows = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? AND s.health_enabled=1", (site["id"],)).fetchall()
    services = [_row(row) for row in rows]
    if not services:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(services))) as executor:
        futures = [executor.submit(_check_service_health, site, item, manual=manual) for item in services]
        return [future.result() for future in concurrent.futures.as_completed(futures)]


def list_health_snapshots(service_id: str, user: User, limit: int = 100) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        owned = database.execute("SELECT 1 FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
        if not owned:
            raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
        rows = database.execute("SELECT * FROM service_navigator_health_snapshots WHERE service_id=? ORDER BY checked_at DESC LIMIT ?", (service_id, max(1, min(limit, 500)))).fetchall()
    return [{"id": row["id"], "checkedAt": row["checked_at"], "status": row["status"], "statusCode": row["status_code"], "latencyMs": row["latency_ms"], "finalUrl": row["final_url"], "error": row["error"], "manual": bool(row["manual"])} for row in rows]


def list_health_events(user: User, limit: int = 100) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT e.*,s.display_name,s.service_name FROM service_navigator_health_events e JOIN service_navigator_services s ON s.id=e.service_id WHERE e.site_id=? ORDER BY e.created_at DESC LIMIT ?", (site["id"], max(1, min(limit, 500)))).fetchall()
    return [{"id": row["id"], "serviceId": row["service_id"], "serviceName": row["display_name"] or row["service_name"], "eventType": row["event_type"], "message": row["message"], "details": json.loads(row["details_json"] or "{}"), "createdAt": row["created_at"]} for row in rows]


def _prune_health_data() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HEALTH_RETENTION_DAYS)).isoformat()
    with conn() as database:
        database.execute("DELETE FROM service_navigator_health_snapshots WHERE checked_at<?", (cutoff,))
        database.execute("DELETE FROM service_navigator_health_events WHERE created_at<?", (cutoff,))
        database.commit()


def collect_due_health_checks() -> None:
    """Scheduler entry point. A broken site or SMTP must not starve peers."""
    _prune_health_data()
    users = {item.id: item for item in list_users() if not item.disabled}
    with conn() as database:
        rows = database.execute("""SELECT site.*,settings.last_checked_at,settings.check_interval_seconds
            FROM service_navigator_sites site JOIN service_navigator_health_settings settings ON settings.site_id=site.id""").fetchall()
    current = datetime.now(timezone.utc)
    for row in rows:
        site = _row(row)
        owner = users.get(site["owner_user_id"])
        if not owner or not can_access_tool(TOOL_ID, owner):
            continue
        last = _parse_time(row["last_checked_at"])
        if last and current - last < timedelta(seconds=int(row["check_interval_seconds"])):
            continue
        with HEALTH_ACTIVE_LOCK:
            if site["id"] in HEALTH_ACTIVE_SITES:
                continue
            HEALTH_ACTIVE_SITES.add(site["id"])
        try:
            _collect_site_health(site, manual=False)
        except Exception:  # noqa: BLE001
            logger.exception("Health collection failed for site %s", site["id"])
        finally:
            with HEALTH_ACTIVE_LOCK:
                HEALTH_ACTIVE_SITES.discard(site["id"])


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


def public_services(site: dict[str, Any]) -> list[dict[str, Any]]:
    with conn() as database:
        rows = database.execute("""SELECT s.*,t.label AS target_label,t.address AS target_address FROM service_navigator_services s
            JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY t.label,t.address,s.display_name,s.port,s.id""", (site["id"],)).fetchall()
    output = []
    for row in rows:
        item = _service_public(row, include_fingerprint=False)
        # The public page gets a coarse health label only. URLs, status codes,
        # timings, errors and historical samples remain owner-only data.
        item["healthMonitored"] = bool(row["health_enabled"])
        for private_key in ("healthEnabled", "healthUrl", "lastHealthCheckedAt", "lastHealthStatusCode", "lastHealthLatencyMs", "lastHealthError"):
            item.pop(private_key, None)
        item.update({"targetLabel": row["target_label"], "targetAddress": row["target_address"], "name": row["display_name"] or row["http_title"] or row["service_name"], "url": (row["navigation_url"] or row["detected_url"]) if row["service_type"] == "http" else "", "command": row["connection_command"] or default_command(row["service_name"], row["target_address"], int(row["port"]))})
        output.append(item)
    return output


def _responsive_layouts(items: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """Derive narrow layouts from the single editable 16-column canvas."""
    output: dict[str, dict[str, dict[str, int]]] = {item["id"]: {"16": item.get("layouts", {}).get("16", {"x": 0, "y": 0})} for item in items}
    ordered = sorted(items, key=lambda item: (item.get("layouts", {}).get("16", {}).get("y", 0), item.get("layouts", {}).get("16", {}).get("x", 0), item["id"]))
    for columns in (12, 8, 4):
        occupied: set[tuple[int, int]] = set()
        for item in ordered:
            width, height = NAV_SIZES[item["size"]]
            for y in range(1000):
                found = False
                for x in range(columns - width + 1):
                    cells = {(column, row) for column in range(x, x + width) for row in range(y, y + height)}
                    if not occupied & cells:
                        occupied |= cells
                        output[item["id"]][str(columns)] = {"x": x, "y": y}
                        found = True
                        break
                if found:
                    break
    return output


def public_navigation(site: dict[str, Any]) -> dict[str, Any]:
    """Return only visitor-safe layout and endpoint data for the SSR page."""
    services = {item["id"]: item for item in public_services(site)}
    detail = _navigation_detail(site["id"])
    pages: list[dict[str, Any]] = []
    for page in detail["pages"]:
        responsive_layouts = _responsive_layouts(page["items"])
        items: list[dict[str, Any]] = []
        for item in page["items"]:
            linked = [services[service_id] for service_id in item["serviceIds"] if service_id in services]
            if not linked:
                continue
            source = item["iconSource"]
            if source == "custom" and item["iconFilename"]:
                icon_url = f"/service-nav/navigation-icon/{item['id']}"
            elif source == "favicon" and item["faviconServiceId"] in services:
                icon_url = services[item["faviconServiceId"]].get("faviconUrl", "")
            else:
                icon_url = ""
            service_types = {entry["serviceType"] for entry in linked}
            if len(service_types) != 1:
                continue
            items.append({
                "id": item["id"], "name": item["name"], "size": item["size"], "iconUrl": icon_url,
                "iconSource": source, "iconText": item.get("iconText", ""), "iconColor": item.get("iconColor", "#4f7cff"),
                "preferenceRevision": item["preferenceRevision"], "serviceType": next(iter(service_types)),
                "layouts": responsive_layouts.get(item["id"], {}), "services": linked,
            })
        pages.append({"id": page["id"], "name": page["name"], "visible": page.get("visible", True), "items": items})
    with conn() as database:
        targets = database.execute("SELECT id,label,address,custom_ports,show_in_navigation FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site["id"],)).fetchall()
    target_pages = [{"id": f"target:{row['id']}", "targetId": row["id"], "name": row["label"], "address": row["address"], "customPorts": row["custom_ports"], "visible": bool(row["show_in_navigation"])} for row in targets]
    return {"breakpoints": list(NAV_BREAKPOINTS), "targetPages": target_pages, "pages": pages, "services": list(services.values())}


def public_icon(service_id: str, site: dict[str, Any]) -> Path | None:
    with conn() as database:
        row = database.execute("""SELECT s.favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?""", (service_id, site["id"])).fetchone()
    if not row or not row["favicon_filename"]:
        return None
    path = (icon_dir() / Path(row["favicon_filename"]).name).resolve()
    return path if path.is_file() and path.parent == icon_dir().resolve() else None


def public_site_for_icon(service_id: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("""SELECT site.slug FROM service_navigator_sites site JOIN service_navigator_targets t ON t.site_id=site.id JOIN service_navigator_services s ON s.target_id=t.id WHERE s.id=?""", (service_id,)).fetchone()
    return public_site(row["slug"]) if row else None


def public_site_for_navigation_icon(item_id: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("""SELECT site.slug FROM service_navigator_sites site
            JOIN service_navigator_nav_pages page ON page.site_id=site.id
            JOIN service_navigator_nav_items item ON item.page_id=page.id WHERE item.id=?""", (item_id,)).fetchone()
    return public_site(row["slug"]) if row else None


def public_navigation_icon(item_id: str, site: dict[str, Any]) -> Path | None:
    with conn() as database:
        row = database.execute("""SELECT item.icon_filename FROM service_navigator_nav_items item
            JOIN service_navigator_nav_pages page ON page.id=item.page_id WHERE item.id=? AND page.site_id=?""", (item_id, site["id"])).fetchone()
    if not row or not row["icon_filename"]:
        return None
    path = (navigation_icon_dir() / Path(row["icon_filename"]).name).resolve()
    return path if path.is_file() and path.parent == navigation_icon_dir().resolve() else None


def public_background(site: dict[str, Any]) -> Path | None:
    source = str(site.get("background_source") or "default")
    if source == "custom":
        path = (background_dir() / Path(str(site.get("background_filename") or "")).name).resolve()
        return path if path.is_file() and path.parent == background_dir().resolve() else None
    if source != "bing":
        return None
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    cached = background_dir() / f"bing-{day}.jpg"
    if cached.is_file():
        return cached
    try:
        request = Request("https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN", headers={"User-Agent": "Pansis-Service-Navigator/1.0"})
        with urlopen(request, timeout=5) as response:
            metadata = json.loads(_read_limited(response, 256 * 1024) or b"{}")
        image = (metadata.get("images") or [{}])[0]
        image_url = str(image.get("url") or "")
        if not image_url.startswith("/"):
            return None
        with urlopen(Request(f"https://www.bing.com{image_url}", headers={"User-Agent": "Pansis-Service-Navigator/1.0"}), timeout=10) as response:
            content = _read_limited(response, BACKGROUND_LIMIT)
        if not content:
            return None
        cached.write_bytes(content)
        return cached
    except (OSError, ValueError, json.JSONDecodeError):
        return None
