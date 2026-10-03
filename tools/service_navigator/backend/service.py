from __future__ import annotations

import concurrent.futures
import html
import ipaddress
import json
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
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User, hash_password, hash_token, list_users, verify_password
from backend.app.services.tool_access_service import can_access_tool

TOOL_ID = "service_navigator"
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


def db_path() -> Path:
    return root_dir() / "data.db"


def conn() -> sqlite3.Connection:
    database = sqlite3.connect(db_path())
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
          visibility TEXT NOT NULL DEFAULT 'private', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_navigator_targets (
          id TEXT PRIMARY KEY, site_id TEXT NOT NULL, label TEXT NOT NULL, address TEXT NOT NULL,
          custom_ports TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
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
          visible INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
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
        CREATE INDEX IF NOT EXISTS idx_service_navigator_targets_site ON service_navigator_targets(site_id);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_services_target ON service_navigator_services(target_id, port);
        CREATE INDEX IF NOT EXISTS idx_service_navigator_runs_site ON service_navigator_scan_runs(site_id, requested_at DESC);
        """
    )
    with RECOVERY_LOCK:
        if not RECOVERY_DONE:
            database.execute("UPDATE service_navigator_scan_runs SET status='interrupted', finished_at=?, error='服务重启导致扫描中断' WHERE status IN ('queued','running')", (now_iso(),))
            RECOVERY_DONE = True
    database.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _site_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {
        "id": item["id"], "title": item["title"], "slug": item["slug"], "description": item["description"],
        "visibility": item["visibility"], "createdAt": item["created_at"], "updatedAt": item["updated_at"],
    }


def _target_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    return {"id": item["id"], "label": item["label"], "address": item["address"], "customPorts": item["custom_ports"], "createdAt": item["created_at"], "updatedAt": item["updated_at"]}


def _service_public(row: sqlite3.Row | dict[str, Any], *, include_fingerprint: bool = True) -> dict[str, Any]:
    item = _row(row) if isinstance(row, sqlite3.Row) else dict(row)
    output = {
        "id": item["id"], "targetId": item["target_id"], "protocol": item["protocol"], "port": int(item["port"]),
        "state": item["state"], "serviceName": item["service_name"], "httpTitle": item["http_title"],
        "detectedUrl": item["detected_url"], "displayName": item["display_name"], "category": item["category"],
        "description": item["description"], "navigationUrl": item["navigation_url"], "connectionCommand": item["connection_command"],
        "visible": bool(item["visible"]), "sortOrder": int(item["sort_order"]), "faviconUrl": "", 
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
    now = now_iso()
    with conn() as database:
        if database.execute("SELECT 1 FROM service_navigator_sites WHERE owner_user_id=?", (user.id,)).fetchone():
            raise ToolboxError("SITE_EXISTS", "每个账号只能创建一个服务导航站", status_code=409, tool_id=TOOL_ID)
        try:
            database.execute("INSERT INTO service_navigator_sites(id,owner_user_id,title,slug,description,visibility,created_at,updated_at) VALUES(?,?,?,?,?,'private',?,?)", (uuid4().hex, user.id, title, slug, description, now, now))
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
    with conn() as database:
        try:
            database.execute("UPDATE service_navigator_sites SET title=?,slug=?,description=?,updated_at=? WHERE id=?", (title, slug, description, now_iso(), site["id"]))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("SLUG_EXISTS", "该站点地址已被占用", status_code=409, tool_id=TOOL_ID) from exc
    return get_site(user) or {}


def delete_site(user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        filenames = [row[0] for row in database.execute("SELECT favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=?", (site["id"],)).fetchall()]
        database.execute("DELETE FROM service_navigator_sites WHERE id=?", (site["id"],))
        database.commit()
    for filename in filenames:
        _remove_icon(filename)


def site_detail(site_id: str, user: User) -> dict[str, Any]:
    with conn() as database:
        site_row = database.execute("SELECT * FROM service_navigator_sites WHERE id=? AND owner_user_id=?", (site_id, user.id)).fetchone()
        if not site_row:
            raise ToolboxError("SITE_NOT_FOUND", "服务导航站不存在", status_code=404, tool_id=TOOL_ID)
        targets = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site_id,)).fetchall()
        services = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.sort_order,s.display_name,s.port", (site_id,)).fetchall()
        runs = database.execute("SELECT * FROM service_navigator_scan_runs WHERE site_id=? ORDER BY requested_at DESC LIMIT 20", (site_id,)).fetchall()
    return {"site": _site_public(site_row), "targets": [_target_public(row) for row in targets], "services": [_service_public(row) for row in services], "runs": [_run_public(row) for row in runs]}


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
        try:
            database.execute("UPDATE service_navigator_targets SET label=?,address=?,custom_ports=?,updated_at=? WHERE id=?", (label, address, ports, now_iso(), target_id))
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
        database.commit()
    for filename in filenames:
        _remove_icon(filename)


def list_targets(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site["id"],)).fetchall()
    return [_target_public(row) for row in rows]


def list_services(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.sort_order,s.display_name,s.port", (site["id"],)).fetchall()
    return [_service_public(row) for row in rows]


def update_service(service_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
        if row is None:
            raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
        values = {
            "display_name": str(payload.get("displayName", row["display_name"]) or "").strip()[:120],
            "category": str(payload.get("category", row["category"]) or "").strip()[:80],
            "description": str(payload.get("description", row["description"]) or "").strip()[:500],
            "navigation_url": _clean_navigation_url(str(payload.get("navigationUrl", row["navigation_url"]) or "")),
            "connection_command": str(payload.get("connectionCommand", row["connection_command"]) or "").strip()[:500],
            "visible": 1 if payload.get("visible", bool(row["visible"])) else 0,
            "sort_order": max(-100000, min(100000, int(payload.get("sortOrder", row["sort_order"]) or 0))),
            "updated_at": now_iso(),
            "id": service_id,
        }
        database.execute("""UPDATE service_navigator_services SET display_name=:display_name,category=:category,description=:description,navigation_url=:navigation_url,connection_command=:connection_command,visible=:visible,sort_order=:sort_order,updated_at=:updated_at WHERE id=:id""", values)
        database.commit()
        updated = database.execute("SELECT * FROM service_navigator_services WHERE id=?", (service_id,)).fetchone()
    return _service_public(updated)


def _clean_navigation_url(value: str) -> str:
    value = value.strip()[:1000]
    if value and not value.startswith(("http://", "https://")):
        raise ToolboxError("INVALID_NAVIGATION_URL", "导航 URL 必须以 http:// 或 https:// 开头", status_code=400, tool_id=TOOL_ID)
    return value


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
                database.execute("""INSERT INTO service_navigator_services(id,target_id,protocol,port,state,service_name,product,version,extra_info,resolved_addresses_json,http_title,favicon_filename,detected_url,connection_command,visible,first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (uuid4().hex, target["id"], "tcp", port, "online", service_name, detected.get("product", ""), detected.get("version", ""), detected.get("extraInfo", ""), json.dumps(detected.get("resolvedAddresses", [])), detected.get("httpTitle", ""), detected.get("faviconFilename", ""), detected.get("detectedUrl", ""), command, 1, now, now, now))
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
        rows = database.execute("""SELECT s.*,t.label AS target_label,t.address AS target_address FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? AND s.visible=1 ORDER BY s.sort_order,s.category,s.display_name,s.port""", (site["id"],)).fetchall()
    output = []
    for row in rows:
        item = _service_public(row, include_fingerprint=False)
        item.update({"targetLabel": row["target_label"], "targetAddress": row["target_address"], "name": row["display_name"] or row["http_title"] or row["service_name"], "url": row["navigation_url"] or row["detected_url"], "command": row["connection_command"] or default_command(row["service_name"], row["target_address"], int(row["port"]))})
        output.append(item)
    return output


def public_icon(service_id: str, site: dict[str, Any]) -> Path | None:
    with conn() as database:
        row = database.execute("""SELECT s.favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=? AND s.visible=1""", (service_id, site["id"])).fetchone()
    if not row or not row["favicon_filename"]:
        return None
    path = (icon_dir() / Path(row["favicon_filename"]).name).resolve()
    return path if path.is_file() and path.parent == icon_dir().resolve() else None


def public_site_for_icon(service_id: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("""SELECT site.slug FROM service_navigator_sites site JOIN service_navigator_targets t ON t.site_id=site.id JOIN service_navigator_services s ON s.target_id=t.id WHERE s.id=?""", (service_id,)).fetchone()
    return public_site(row["slug"]) if row else None
