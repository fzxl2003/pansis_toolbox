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
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
    urlopen,
)
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import (
    User,
    hash_password,
    hash_token,
    list_users,
    verify_password,
)
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
SERVICE_TEMPLATE_COMPAT_DATABASES: set[str] = set()
SERVICE_TEMPLATE_COMPAT_LOCK = threading.Lock()
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
PORT_SERVICE_TEMPLATES = {
    "generic": {
        "label": "通用端口服务",
        "description": "通过指定地址和端口访问该服务。",
        "command": "{host}:{port}",
    },
    "ssh": {
        "label": "SSH",
        "description": "通过 SSH 安全远程登录服务器。",
        "command": "ssh -p {port} <user>@{host}",
    },
    "sftp": {
        "label": "SFTP",
        "description": "通过 SFTP 安全传输文件。",
        "command": "sftp -P {port} <user>@{host}",
    },
    "rdp": {
        "label": "远程桌面（RDP）",
        "description": "通过远程桌面客户端连接此主机。",
        "command": "xfreerdp /v:{host}:{port} /u:<user>",
    },
    "vnc": {
        "label": "VNC",
        "description": "通过 VNC 客户端查看和控制远程桌面。",
        "command": "vncviewer {host}:{port}",
    },
    "ftp": {
        "label": "FTP",
        "description": "通过 FTP 客户端传输文件。",
        "command": "ftp {host} {port}",
    },
    "smb": {
        "label": "SMB 文件共享",
        "description": "通过 SMB 客户端访问共享文件。",
        "command": "smbclient //{host}/<share> -p {port} -U <user>",
    },
}
NAV_BREAKPOINTS = (16, 12, 8, 4)
NAV_SIZES: dict[str, tuple[int, int]] = {
    "small": (1, 1), "medium": (2, 2), "large": (4, 4), "wide": (4, 2),
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
          card_opacity INTEGER NOT NULL DEFAULT 84,
          background_filename TEXT NOT NULL DEFAULT '', show_all_services INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_targets (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, label TEXT NOT NULL, address TEXT NOT NULL,
          custom_ports TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
          show_in_navigation INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
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
          command_description TEXT NOT NULL DEFAULT '', service_template TEXT NOT NULL DEFAULT 'generic',
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
          id TEXT PRIMARY KEY, page_id TEXT NOT NULL, icon_id TEXT NOT NULL, name TEXT NOT NULL,
          size TEXT NOT NULL DEFAULT 'small', icon_source TEXT NOT NULL DEFAULT 'none',
          icon_filename TEXT NOT NULL DEFAULT '', icon_text TEXT NOT NULL DEFAULT '', icon_color TEXT NOT NULL DEFAULT '#4f7cff', favicon_service_id TEXT,
          preference_revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(page_id) REFERENCES service_navigator_nav_pages(id) ON DELETE CASCADE,
          FOREIGN KEY(icon_id) REFERENCES service_navigator_nav_icons(id) ON DELETE CASCADE,
          FOREIGN KEY(favicon_service_id) REFERENCES service_navigator_services(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_icons (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, name TEXT NOT NULL,
          icon_source TEXT NOT NULL DEFAULT 'text', icon_filename TEXT NOT NULL DEFAULT '',
          icon_text TEXT NOT NULL DEFAULT '', icon_color TEXT NOT NULL DEFAULT '#4f7cff',
          favicon_service_id TEXT, detected_service_id TEXT,
          destination_type TEXT NOT NULL DEFAULT 'service', external_url TEXT NOT NULL DEFAULT '',
          external_favicon_filename TEXT NOT NULL DEFAULT '',
          preference_revision INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(site_id) REFERENCES service_navigator_sites(id) ON DELETE CASCADE,
          FOREIGN KEY(favicon_service_id) REFERENCES service_navigator_services(id) ON DELETE SET NULL,
          FOREIGN KEY(detected_service_id) REFERENCES service_navigator_services(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_nav_icon_services (
          icon_id TEXT NOT NULL, service_id TEXT NOT NULL,
          PRIMARY KEY(icon_id, service_id),
          FOREIGN KEY(icon_id) REFERENCES service_navigator_nav_icons(id) ON DELETE CASCADE,
          FOREIGN KEY(service_id) REFERENCES service_navigator_services(id) ON DELETE CASCADE
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
        CREATE INDEX IF NOT EXISTS idx_service_navigator_nav_icons_site ON service_navigator_nav_icons(site_id, sort_order);
        """
    )
    _ensure_column(database, "service_navigator_services", "health_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_services", "service_type", "TEXT NOT NULL DEFAULT 'port'")
    _ensure_column(database, "service_navigator_services", "command_description", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_services", "service_template", "TEXT NOT NULL DEFAULT 'generic'")
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
    _ensure_column(database, "service_navigator_sites", "card_opacity", "INTEGER NOT NULL DEFAULT 84")
    _ensure_column(database, "service_navigator_nav_items", "icon_text", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_nav_items", "icon_color", "TEXT NOT NULL DEFAULT '#4f7cff'")
    _ensure_column(database, "service_navigator_nav_items", "icon_id", "TEXT")
    _ensure_column(database, "service_navigator_targets", "show_in_navigation", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_targets", "sort_order", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(database, "service_navigator_nav_pages", "visible", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(database, "service_navigator_nav_icons", "destination_type", "TEXT NOT NULL DEFAULT 'service'")
    _ensure_column(database, "service_navigator_nav_icons", "external_url", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(database, "service_navigator_nav_icons", "external_favicon_filename", "TEXT NOT NULL DEFAULT ''")
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
    with SERVICE_TEMPLATE_COMPAT_LOCK:
        needs_template_compat = database_key not in SERVICE_TEMPLATE_COMPAT_DATABASES
        if needs_template_compat:
            SERVICE_TEMPLATE_COMPAT_DATABASES.add(database_key)
    if needs_template_compat:
        database.execute("""UPDATE service_navigator_services SET command_description=connection_command
            WHERE command_description='' AND connection_command<>''""")
        database.execute("""UPDATE service_navigator_services SET service_template='generic'
            WHERE service_template NOT IN ('generic','ssh','sftp','rdp','vnc','ftp','smb')""")
    _migrate_navigation_icons(database)
    with RECOVERY_LOCK:
        if not RECOVERY_DONE:
            database.execute("UPDATE service_navigator_scan_runs SET status='interrupted', finished_at=?, error='服务重启导致扫描中断' WHERE status IN ('queued','running')", (now_iso(),))
            RECOVERY_DONE = True
    database.commit()


def _ensure_column(database: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in database.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        database.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_navigation_icons(database: sqlite3.Connection) -> None:
    """Convert inline page items to a reusable, site-wide icon library."""
    legacy = database.execute("""SELECT i.*,p.site_id FROM service_navigator_nav_items i
        JOIN service_navigator_nav_pages p ON p.id=i.page_id WHERE i.icon_id IS NULL""").fetchall()
    for row in legacy:
        icon_id, now = uuid4().hex, now_iso()
        source = row["icon_source"] if row["icon_source"] in {"text", "favicon", "custom"} else "text"
        database.execute("""INSERT INTO service_navigator_nav_icons(id,site_id,name,icon_source,icon_filename,
            icon_text,icon_color,favicon_service_id,preference_revision,sort_order,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (icon_id, row["site_id"], row["name"], source,
            row["icon_filename"], row["icon_text"] or row["name"][:1], row["icon_color"], row["favicon_service_id"],
            int(row["preference_revision"] or 1), 0, now, now))
        links = database.execute("SELECT service_id FROM service_navigator_nav_item_services WHERE item_id=?", (row["id"],)).fetchall()
        database.executemany("INSERT OR IGNORE INTO service_navigator_nav_icon_services(icon_id,service_id) VALUES(?,?)", [(icon_id, link["service_id"]) for link in links])
        database.execute("UPDATE service_navigator_nav_items SET icon_id=?,updated_at=? WHERE id=?", (icon_id, now, row["id"]))
    for service in database.execute("""SELECT s.*,t.site_id FROM service_navigator_services s
            JOIN service_navigator_targets t ON t.id=s.target_id""").fetchall():
        if database.execute("SELECT 1 FROM service_navigator_nav_icon_services WHERE service_id=? LIMIT 1", (service["id"],)).fetchone():
            continue
        name = service["display_name"] or service["http_title"] or service["service_name"] or f"TCP/{service['port']}"
        source = "favicon" if service["favicon_filename"] else "text"
        icon_id, now = uuid4().hex, now_iso()
        database.execute("""INSERT INTO service_navigator_nav_icons(id,site_id,name,icon_source,icon_filename,
            icon_text,icon_color,favicon_service_id,detected_service_id,preference_revision,sort_order,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (icon_id, service["site_id"], name, source, "",
            name[:1], "#4f7cff", service["id"] if source == "favicon" else None, service["id"], 1, 0, now, now))
        database.execute("INSERT INTO service_navigator_nav_icon_services(icon_id,service_id) VALUES(?,?)", (icon_id, service["id"]))


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _site_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {
        "id": item["id"], "title": item["title"], "slug": item["slug"], "description": item["description"],
        "visibility": item["visibility"], "backgroundSource": item.get("background_source", "default"),
        "backgroundFilename": item.get("background_filename", ""), "showAllServices": bool(item.get("show_all_services", 1)),
        "theme": _normalise_site_theme(item.get("theme", "auto")), "accentColor": _normalise_accent_color(item.get("accent_color", "#4f7cff")),
        "cardOpacity": _normalise_card_opacity(item.get("card_opacity", 84)),
        "createdAt": item["created_at"], "updatedAt": item["updated_at"],
    }


def _target_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {"id": item["id"], "label": item["label"], "address": item["address"], "customPorts": item["custom_ports"], "sortOrder": int(item.get("sort_order", 0)), "showInNavigation": bool(item.get("show_in_navigation", 1)), "createdAt": item["created_at"], "updatedAt": item["updated_at"]}


def _service_public(row: sqlite3.Row | dict[str, Any], *, include_fingerprint: bool = True) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    output = {
        "id": item["id"], "targetId": item["target_id"], "protocol": item["protocol"], "port": int(item["port"]),
        "state": item["state"], "serviceName": item["service_name"], "httpTitle": item["http_title"],
        "detectedUrl": item["detected_url"], "displayName": item["display_name"],
        "description": item["description"], "navigationUrl": item["navigation_url"],
        "commandDescription": item.get("command_description") or item["connection_command"],
        "serviceTemplate": item.get("service_template") or "generic", "serviceType": item["service_type"],
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


def _normalise_card_opacity(value: Any) -> int:
    try:
        opacity = int(value)
    except (TypeError, ValueError):
        return 84
    return min(100, max(20, opacity))


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


__all__ = [
    "ACCESS_DAYS",
    "ACTIVE_LOCK",
    "ACTIVE_RUNS",
    "BACKGROUND_LIMIT",
    "BACKGROUND_SUFFIXES",
    "COMMAND_TEMPLATES",
    "PORT_SERVICE_TEMPLATES",
    "COMMON_TCP_PORTS",
    "FAVICON_LIMIT",
    "HEALTH_ACTIVE_LOCK",
    "HEALTH_ACTIVE_SITES",
    "HEALTH_COMPAT_DATABASES",
    "HEALTH_COMPAT_LOCK",
    "HEALTH_CONFIRM_DEFAULT",
    "HEALTH_DEFAULT_INTERVAL",
    "HEALTH_MAX_INTERVAL",
    "HEALTH_MIN_INTERVAL",
    "HEALTH_REPEAT_DEFAULT",
    "HEALTH_RETENTION_DAYS",
    "HEALTH_SEMAPHORE",
    "HEALTH_TIMEOUT",
    "HOST_RE",
    "HOST_SCAN_TIMEOUT",
    "HTTP_METADATA_LIMIT",
    "MAX_DNS_ADDRESSES",
    "MAX_TARGETS",
    "NAV_BREAKPOINTS",
    "NAV_ICON_LIMIT",
    "NAV_ICON_SUFFIXES",
    "NAV_SIZES",
    "PORT_CONNECT_TIMEOUT",
    "PORT_PROBE_TIMEOUT",
    "PORT_SCAN_WORKERS",
    "PORT_SERVICE_HINTS",
    "RECOVERY_DONE",
    "RECOVERY_LOCK",
    "SCAN_SEMAPHORE",
    "SLUG_RE",
    "TOOL_ID",
    "VISITOR_DAYS",
    "WEB_PORTS",
    "Any",
    "HTTPError",
    "HTTPRedirectHandler",
    "HTTPSHandler",
    "Path",
    "Request",
    "ToolboxError",
    "URLError",
    "User",
    "_compact_port_ranges",
    "_ensure_column",
    "_migrate_navigation_icons",
    "_normalise_accent_color",
    "_normalise_card_opacity",
    "_normalise_site_theme",
    "_row",
    "_run_public",
    "_service_public",
    "_site_public",
    "_target_public",
    "background_dir",
    "build_opener",
    "can_access_tool",
    "concurrent",
    "conn",
    "datetime",
    "db_path",
    "get_settings",
    "hash_password",
    "hash_token",
    "html",
    "icon_dir",
    "init_database",
    "ipaddress",
    "json",
    "list_users",
    "logger",
    "logging",
    "navigation_icon_dir",
    "normalize_address",
    "normalize_ports",
    "normalize_slug",
    "now_iso",
    "platform_send_email",
    "re",
    "root_dir",
    "secrets",
    "socket",
    "sqlite3",
    "ssl",
    "threading",
    "time",
    "timedelta",
    "timezone",
    "urljoin",
    "urlopen",
    "urlparse",
    "uuid4",
    "verify_password",
]
