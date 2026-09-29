"""Private storage, parsing and publishing for the Clash subscription manager.

The module deliberately keeps network acquisition separate from parsing.  A
failed refresh can therefore never destroy the last successful snapshot or an
already published configuration.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import yaml
from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import connection_context, list_user_tool_dbs, user_tool_connection_context
from backend.app.services.auth_service import User
from backend.app.services.data_management import DataCategory, register_tool_categories

TOOL_ID = "clash_subscription_manager"
DEFAULT_REFRESH_SECONDS = 6 * 3600
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
SUPPORTED_URIS = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic"}
MIHOMO_ONLY = {"vless", "hysteria", "hysteria2", "tuic", "wireguard"}
CLASH_STRUCTURED_TYPES = {"http", "socks5", "snell", "wireguard"}
_initialized: set[str] = set()
_init_lock = threading.Lock()

register_tool_categories(TOOL_ID, [
    DataCategory("configuration", ["csm_sources", "csm_nodes", "csm_node_sources", "csm_profiles", "csm_profile_selections", "csm_rule_sets", "csm_published_snapshots"], None, "订阅源、节点、配置和最后有效发布版本"),
    DataCategory("history", ["csm_source_snapshots", "csm_refresh_runs", "csm_probe_results"], "created_at", "订阅刷新、快照和 TCP 探测记录"),
    DataCategory("public_tokens", ["csm_public_tokens"], None, "公开订阅令牌索引", storage="platform_db", user_id_column="user_id"),
])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _fernet() -> Fernet:
    secret = get_settings().session_secret.encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ToolboxError("CSM_DECRYPT_FAILED", "保存的敏感数据无法解密。", status_code=500) from exc


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme:
        return ""
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}" + ("?…" if parts.query else "")


def _require_http_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ToolboxError("INVALID_SOURCE_URL", "订阅地址必须是有效的 HTTP(S) URL。", status_code=422)
    return url.strip()


def init_database(user_id: str) -> None:
    """Create a user's isolated data store and the global token lookup table."""
    with _init_lock:
        if user_id in _initialized:
            return
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS csm_sources (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, url_encrypted TEXT NOT NULL,
              user_agent TEXT NOT NULL DEFAULT '', refresh_seconds INTEGER NOT NULL DEFAULT 21600,
              enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'never',
              last_success_at TEXT, last_attempt_at TEXT, next_refresh_at TEXT,
              last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_source_snapshots (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, content_encrypted TEXT NOT NULL,
              content_hash TEXT NOT NULL, parsed_count INTEGER NOT NULL DEFAULT 0,
              unsupported_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              FOREIGN KEY(source_id) REFERENCES csm_sources(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_nodes (
              id TEXT PRIMARY KEY, stable_identity TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL, protocol TEXT NOT NULL, server TEXT NOT NULL DEFAULT '', port INTEGER,
              config_json TEXT NOT NULL, supported_output INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_node_sources (
              node_id TEXT NOT NULL, source_id TEXT NOT NULL, source_alias TEXT NOT NULL DEFAULT '',
              last_seen_at TEXT NOT NULL, PRIMARY KEY(node_id, source_id),
              FOREIGN KEY(node_id) REFERENCES csm_nodes(id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES csm_sources(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_profiles (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, target_kernel TEXT NOT NULL DEFAULT 'mihomo',
              settings_json TEXT NOT NULL DEFAULT '{}', rule_set_id TEXT, token_encrypted TEXT NOT NULL,
              published_at TEXT, published_status TEXT NOT NULL DEFAULT 'draft', last_validation_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_profile_selections (
              profile_id TEXT NOT NULL, node_identity TEXT NOT NULL, selected_at TEXT NOT NULL,
              PRIMARY KEY(profile_id,node_identity), FOREIGN KEY(profile_id) REFERENCES csm_profiles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_rule_sets (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, rules_json TEXT NOT NULL DEFAULT '[]',
              providers_json TEXT NOT NULL DEFAULT '{}', groups_json TEXT NOT NULL DEFAULT '[]',
              import_meta_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_published_snapshots (
              id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, content_encrypted TEXT NOT NULL,
              content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(profile_id) REFERENCES csm_profiles(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_refresh_runs (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
              finished_at TEXT, duration_ms INTEGER, nodes_before INTEGER NOT NULL DEFAULT 0, nodes_after INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_probe_results (
              id TEXT PRIMARY KEY, node_id TEXT NOT NULL, reachable INTEGER NOT NULL, dns_address TEXT NOT NULL DEFAULT '',
              latency_ms INTEGER, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              FOREIGN KEY(node_id) REFERENCES csm_nodes(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS csm_nodes_seen ON csm_nodes(last_seen_at);
            CREATE INDEX IF NOT EXISTS csm_runs_source ON csm_refresh_runs(source_id, created_at DESC);
            """)
        with connection_context() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS csm_public_tokens (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, profile_id TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        _initialized.add(user_id)


def _row_source(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "url": _redact_url(_decrypt(row["url_encrypted"])),
            "userAgent": row["user_agent"], "refreshSeconds": row["refresh_seconds"], "enabled": bool(row["enabled"]),
            "status": row["status"], "lastSuccessAt": row["last_success_at"], "lastAttemptAt": row["last_attempt_at"],
            "nextRefreshAt": row["next_refresh_at"], "lastError": row["last_error"], "createdAt": row["created_at"]}


def list_sources(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_row_source(r) for r in conn.execute("SELECT * FROM csm_sources ORDER BY created_at DESC")]


def create_source(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    url = _require_http_url(str(data.get("url", "")))
    name = str(data.get("name") or _redact_url(url))[:120]
    refresh_seconds = max(60, min(7 * 86400, int(data.get("refreshSeconds") or DEFAULT_REFRESH_SECONDS)))
    now, source_id = _now(), _id()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("INSERT INTO csm_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (source_id, name, _encrypt(url), str(data.get("userAgent") or "")[:300], refresh_seconds, int(bool(data.get("enabled", True))), "never", None, None, now, "", now, now))
        return _row_source(conn.execute("SELECT * FROM csm_sources WHERE id=?", (source_id,)).fetchone())


def update_source(source_id: str, data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_sources WHERE id=?", (source_id,)).fetchone()
        if not row: _not_found("订阅源")
        values = {"name": row["name"], "url_encrypted": row["url_encrypted"], "user_agent": row["user_agent"], "refresh_seconds": row["refresh_seconds"], "enabled": row["enabled"]}
        if "url" in data: values["url_encrypted"] = _encrypt(_require_http_url(str(data["url"])))
        if "name" in data: values["name"] = str(data["name"]).strip()[:120] or row["name"]
        if "userAgent" in data: values["user_agent"] = str(data["userAgent"] or "")[:300]
        if "refreshSeconds" in data: values["refresh_seconds"] = max(60, min(7 * 86400, int(data["refreshSeconds"])))
        if "enabled" in data: values["enabled"] = int(bool(data["enabled"]))
        conn.execute("""UPDATE csm_sources SET name=:name,url_encrypted=:url_encrypted,user_agent=:user_agent,
                      refresh_seconds=:refresh_seconds,enabled=:enabled,updated_at=:now WHERE id=:id""", {**values, "now": _now(), "id": source_id})
        return _row_source(conn.execute("SELECT * FROM csm_sources WHERE id=?", (source_id,)).fetchone())


def delete_source(source_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_sources WHERE id=?", (source_id,)).rowcount == 0: _not_found("订阅源")


def _not_found(label: str) -> None:
    raise ToolboxError("CSM_NOT_FOUND", f"{label}不存在。", status_code=404)


def _b64decode(value: str) -> bytes:
    raw = value.strip().encode("utf-8")
    raw += b"=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def _hash(data: Any) -> str:
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()


def _query_one(query: str, key: str) -> str:
    return (parse_qs(query).get(key) or [""])[0]


def _node(name: str, protocol: str, server: str, port: int | None, config: dict[str, Any], supported: bool = True) -> dict[str, Any]:
    protocol = protocol.lower()
    config = {k: v for k, v in config.items() if v not in (None, "", [], {})}
    config.update({"name": name or f"{protocol}-{server}:{port or ''}", "type": protocol, "server": server, "port": port})
    identity = _hash({"protocol": protocol, "server": server.lower(), "port": port, "name": name})
    fingerprint = _hash({k: v for k, v in config.items() if k != "name"})
    return {"stable_identity": identity, "fingerprint": fingerprint, "name": config["name"], "protocol": protocol,
            "server": server, "port": port, "config": config, "supported_output": supported}


def parse_uri(uri: str) -> dict[str, Any] | None:
    """Parse a supported share URI without silently inventing fields."""
    raw = uri.strip()
    if not raw or "://" not in raw: return None
    kind = raw.split("://", 1)[0].lower()
    if kind not in SUPPORTED_URIS: return None
    if kind == "vmess":
        try:
            obj = json.loads(_b64decode(raw.split("://", 1)[1]).decode("utf-8-sig"))
            server, port = str(obj.get("add", "")), int(obj.get("port") or 0) or None
            return _node(str(obj.get("ps") or "vmess"), kind, server, port, {"uuid": obj.get("id"), "alterId": obj.get("aid"), "cipher": obj.get("scy"), "network": obj.get("net"), "tls": obj.get("tls"), "servername": obj.get("sni"), "ws-opts": {"path": obj.get("path"), "headers": {"Host": obj.get("host")} if obj.get("host") else {}}})
        except (ValueError, UnicodeError, TypeError): return None
    if kind == "ssr":
        try:
            decoded = _b64decode(raw.split("://", 1)[1]).decode("utf-8")
            base, _, query = decoded.partition("/?")
            server, port, protocol, method, obfs, password = base.split(":", 5)
            params = parse_qs(query)
            name = _b64decode(params.get("remarks", [""])[0]).decode("utf-8", "replace") if params.get("remarks") else "ssr"
            return _node(name, kind, server, int(port), {"cipher": method, "password": _b64decode(password).decode("utf-8", "replace"), "protocol": protocol, "obfs": obfs, "protocol-param": _b64decode(params.get("protoparam", [""])[0]).decode("utf-8", "replace") if params.get("protoparam") else "", "obfs-param": _b64decode(params.get("obfsparam", [""])[0]).decode("utf-8", "replace") if params.get("obfsparam") else ""})
        except (ValueError, UnicodeError, TypeError): return None
    if kind == "ss":
        try:
            rest = raw[5:]; main, sep, fragment = rest.partition("#"); name = unquote(fragment) if sep else "ss"
            main, _, query = main.partition("?"); plugin = _query_one(query, "plugin")
            if "@" not in main:
                decoded = _b64decode(main).decode("utf-8"); credentials, host = decoded.rsplit("@", 1)
            else:
                credentials, host = main.rsplit("@", 1)
                try: credentials = _b64decode(credentials).decode("utf-8")
                except (ValueError, UnicodeError): credentials = unquote(credentials)
            method, password = credentials.split(":", 1); parsed = urlsplit("//" + host)
            return _node(name, kind, parsed.hostname or "", parsed.port, {"cipher": method, "password": password, "plugin": unquote(plugin)})
        except (ValueError, UnicodeError): return None
    parsed = urlsplit(raw)
    try: port = parsed.port
    except ValueError: return None
    server, name, q = parsed.hostname or "", unquote(parsed.fragment) or kind, parse_qs(parsed.query)
    credentials = unquote(parsed.username or "")
    config: dict[str, Any] = {"password": unquote(parsed.password or "") or credentials, "uuid": credentials if kind in {"vless", "hysteria", "hysteria2", "tuic"} else "", "sni": _query_one(parsed.query, "sni") or _query_one(parsed.query, "peer"), "servername": _query_one(parsed.query, "sni"), "network": _query_one(parsed.query, "type"), "tls": _query_one(parsed.query, "security") or ("tls" if kind == "trojan" else ""), "flow": _query_one(parsed.query, "flow"), "udp": _query_one(parsed.query, "udp")}
    if kind in {"hysteria", "hysteria2"}: config.update({"obfs": _query_one(parsed.query, "obfs"), "obfs-password": _query_one(parsed.query, "obfs-password"), "up": _query_one(parsed.query, "up"), "down": _query_one(parsed.query, "down")})
    if kind == "tuic": config.update({"uuid": credentials, "password": unquote(parsed.password or ""), "congestion-controller": _query_one(parsed.query, "congestion_control")})
    if kind == "vless": config["uuid"] = credentials
    return _node(name, kind, server, port, config)


def _clash_node(value: dict[str, Any]) -> dict[str, Any] | None:
    protocol = str(value.get("type") or "").lower()
    if not protocol: return None
    server = str(value.get("server") or "")
    try: port = int(value.get("port")) if value.get("port") is not None else None
    except (ValueError, TypeError): port = None
    supported = protocol in SUPPORTED_URIS or protocol in CLASH_STRUCTURED_TYPES
    return _node(str(value.get("name") or protocol), protocol, server, port, dict(value), supported)


def parse_subscription(content: bytes | str) -> tuple[list[dict[str, Any]], int]:
    """Accept Clash YAML, provider payload, base64 subscriptions and URI lines."""
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", "replace")
    else: text = content.lstrip("\ufeff")
    items: list[dict[str, Any]] = []; unsupported = 0
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        loaded = None
    if isinstance(loaded, dict):
        proxies = loaded.get("proxies") or (loaded.get("payload") if isinstance(loaded.get("payload"), list) else [])
        if isinstance(proxies, list):
            for proxy in proxies:
                if isinstance(proxy, dict):
                    parsed = _clash_node(proxy)
                    if parsed: items.append(parsed); unsupported += int(not parsed["supported_output"])
            return items, unsupported
    candidates = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(candidates) == 1 and "://" not in candidates[0]:
        try:
            decoded = _b64decode(candidates[0]).decode("utf-8-sig", "replace")
            candidates = [line.strip() for line in decoded.splitlines() if line.strip()]
        except (ValueError, UnicodeError): pass
    for line in candidates:
        parsed = parse_uri(line)
        if parsed: items.append(parsed)
        elif "://" in line: unsupported += 1
    return items, unsupported


def _download(url: str, user_agent: str = "") -> bytes:
    headers = {"User-Agent": user_agent or "pansis-clash-subscription-manager/1.0"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, max_redirects=MAX_REDIRECTS, headers=headers) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []; size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ToolboxError("SOURCE_TOO_LARGE", "订阅响应超过 10 MiB 限制。", status_code=422)
                    chunks.append(chunk)
                return b"".join(chunks)
    except ToolboxError: raise
    except httpx.HTTPError as exc:
        raise ToolboxError("SOURCE_FETCH_FAILED", f"获取订阅失败：{str(exc)[:240]}", status_code=422) from exc


def _expand_providers(content: bytes, user_agent: str) -> list[bytes]:
    """Expand only first-level remote providers; no recursive fetch is allowed."""
    result = [content]
    try: config = yaml.safe_load(content.decode("utf-8-sig", "replace"))
    except yaml.YAMLError: return result
    providers = config.get("proxy-providers") if isinstance(config, dict) else None
    if not isinstance(providers, dict): return result
    for provider in providers.values():
        if not isinstance(provider, dict) or str(provider.get("type", "http")).lower() != "http": continue
        url = provider.get("url")
        if not isinstance(url, str): continue
        try: result.append(_download(_require_http_url(url), user_agent))
        except ToolboxError: continue  # The enclosing source remains a valid snapshot.
    return result


def _source_node_count(conn: sqlite3.Connection, source_id: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM csm_node_sources WHERE source_id=?", (source_id,)).fetchone()[0])


def _store_nodes(conn: sqlite3.Connection, source_id: str, nodes: Iterable[dict[str, Any]]) -> int:
    now = _now(); count = 0
    for item in nodes:
        row = conn.execute("SELECT id FROM csm_nodes WHERE fingerprint=?", (item["fingerprint"],)).fetchone()
        if row:
            node_id = row["id"]
            # Keep the original stable identity when identical material is
            # encountered under another source alias; selections remain valid.
            conn.execute("UPDATE csm_nodes SET name=?,protocol=?,server=?,port=?,config_json=?,supported_output=?,last_seen_at=? WHERE id=?", (item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]), int(item["supported_output"]), now, node_id))
        else:
            node_id = _id()
            # Stable identities are intentionally unique across a user's pool.
            conflict = conn.execute("SELECT id FROM csm_nodes WHERE stable_identity=?", (item["stable_identity"],)).fetchone()
            if conflict:
                node_id = conflict["id"]
                conn.execute("UPDATE csm_nodes SET fingerprint=?,name=?,protocol=?,server=?,port=?,config_json=?,supported_output=?,last_seen_at=? WHERE id=?", (item["fingerprint"], item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]), int(item["supported_output"]), now, node_id))
            else:
                conn.execute("INSERT INTO csm_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (node_id, item["stable_identity"], item["fingerprint"], item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]), int(item["supported_output"]), now, now))
        conn.execute("INSERT INTO csm_node_sources(node_id,source_id,source_alias,last_seen_at) VALUES(?,?,?,?) ON CONFLICT(node_id,source_id) DO UPDATE SET source_alias=excluded.source_alias,last_seen_at=excluded.last_seen_at", (node_id, source_id, item["name"], now))
        count += 1
    return count


def refresh_source(source_id: str, user: User, *, auto_rebuild: bool = True) -> dict[str, Any]:
    init_database(user.id); started, run_id = _now(), _id(); clock = time.monotonic()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        source = conn.execute("SELECT * FROM csm_sources WHERE id=?", (source_id,)).fetchone()
        if not source: _not_found("订阅源")
        before = _source_node_count(conn, source_id)
        conn.execute("INSERT INTO csm_refresh_runs(id,source_id,status,started_at,nodes_before,created_at) VALUES(?,?,?,?,?,?)", (run_id, source_id, "running", started, before, started))
        conn.execute("UPDATE csm_sources SET last_attempt_at=?,updated_at=? WHERE id=?", (started, started, source_id))
    try:
        body = _download(_decrypt(source["url_encrypted"]), source["user_agent"])
        parsed: list[dict[str, Any]] = []; unsupported = 0
        for payload in _expand_providers(body, source["user_agent"]):
            found, count = parse_subscription(payload); parsed.extend(found); unsupported += count
        if not parsed:
            raise ToolboxError("SOURCE_PARSE_FAILED", "未从订阅中解析到支持的节点。", status_code=422)
        # Same node may occur in its root and a provider. Fingerprint merging happens in storage.
        digest, now = hashlib.sha256(body).hexdigest(), _now()
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            _store_nodes(conn, source_id, parsed); after = _source_node_count(conn, source_id)
            conn.execute("INSERT INTO csm_source_snapshots VALUES(?,?,?,?,?,?,?)", (_id(), source_id, _encrypt(body.decode("utf-8", "replace")), digest, len(parsed), unsupported, now))
            conn.execute("""UPDATE csm_sources SET status='healthy',last_success_at=?,next_refresh_at=?,last_error='',updated_at=? WHERE id=?""", (now, datetime.fromtimestamp(time.time() + int(source["refresh_seconds"]), timezone.utc).isoformat(), now, source_id))
            conn.execute("UPDATE csm_refresh_runs SET status='success',finished_at=?,duration_ms=?,nodes_after=? WHERE id=?", (now, int((time.monotonic()-clock)*1000), after, run_id))
        if auto_rebuild: _rebuild_published_profiles(user)
        return {"runId": run_id, "status": "success", "parsedNodes": len(parsed), "unsupportedNodes": unsupported, "nodesBefore": before, "nodesAfter": after}
    except Exception as exc:
        message = exc.message if isinstance(exc, ToolboxError) else str(exc)
        now = _now()
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            conn.execute("UPDATE csm_sources SET status='error',last_error=?,updated_at=?,next_refresh_at=? WHERE id=?", (message[:500], now, datetime.fromtimestamp(time.time() + int(source["refresh_seconds"]), timezone.utc).isoformat(), source_id))
            conn.execute("UPDATE csm_refresh_runs SET status='failed',finished_at=?,duration_ms=?,error=? WHERE id=?", (now, int((time.monotonic()-clock)*1000), message[:500], run_id))
        if isinstance(exc, ToolboxError): raise
        raise ToolboxError("SOURCE_REFRESH_FAILED", f"刷新失败：{message[:240]}", status_code=422) from exc


def _node_dict(row: sqlite3.Row, source_names: list[str] | None = None, probe: sqlite3.Row | None = None) -> dict[str, Any]:
    return {"id": row["id"], "stableIdentity": row["stable_identity"], "name": row["name"], "protocol": row["protocol"], "server": row["server"], "port": row["port"], "config": _loads(row["config_json"], {}), "supportedOutput": bool(row["supported_output"]), "lastSeenAt": row["last_seen_at"], "sources": source_names or [], "tcp": None if not probe else {"reachable": bool(probe["reachable"]), "latencyMs": probe["latency_ms"], "error": probe["error"], "checkedAt": probe["created_at"]}}


def list_nodes(user: User, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    init_database(user.id); filters = filters or {}; terms, values = [], []
    for key, column in (("protocol", "n.protocol"), ("sourceId", "ns.source_id")):
        if filters.get(key): terms.append(f"{column}=?"); values.append(str(filters[key]))
    if filters.get("search"):
        terms.append("(n.name LIKE ? OR n.server LIKE ?)"); values.extend([f"%{filters['search']}%", f"%{filters['search']}%"])
    where = " WHERE " + " AND ".join(terms) if terms else ""
    query = f"""SELECT DISTINCT n.*, (SELECT group_concat(s.name,'|') FROM csm_node_sources ns2 JOIN csm_sources s ON s.id=ns2.source_id WHERE ns2.node_id=n.id) AS source_names,
      (SELECT pr.id FROM csm_probe_results pr WHERE pr.node_id=n.id ORDER BY pr.created_at DESC LIMIT 1) AS probe_id FROM csm_nodes n LEFT JOIN csm_node_sources ns ON ns.node_id=n.id{where} ORDER BY n.last_seen_at DESC"""
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        result=[]
        for row in conn.execute(query, values):
            probe = conn.execute("SELECT * FROM csm_probe_results WHERE id=?", (row["probe_id"],)).fetchone() if row["probe_id"] else None
            result.append(_node_dict(row, str(row["source_names"] or "").split("|") if row["source_names"] else [], probe))
        return result


def _compatible(protocol: str, kernel: str) -> bool:
    return kernel == "mihomo" or protocol not in MIHOMO_ONLY


def _profile_row(row: sqlite3.Row, public_token: str | None = None) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "targetKernel": row["target_kernel"], "settings": _loads(row["settings_json"], {}), "ruleSetId": row["rule_set_id"], "publishedAt": row["published_at"], "publishedStatus": row["published_status"], "validation": _loads(row["last_validation_json"], []), "subscriptionToken": public_token, "createdAt": row["created_at"]}


def list_profiles(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        result = []
        for row in conn.execute("SELECT * FROM csm_profiles ORDER BY created_at DESC"):
            profile = _profile_row(row, _decrypt(row["token_encrypted"]))
            profile["selectedStableIdentities"] = [item["node_identity"] for item in conn.execute("SELECT node_identity FROM csm_profile_selections WHERE profile_id=? ORDER BY selected_at", (row["id"],))]
            result.append(profile)
        return result


def _create_token_index(user_id: str, profile_id: str, token: str) -> None:
    with connection_context() as conn:
        now = _now(); conn.execute("INSERT INTO csm_public_tokens VALUES(?,?,?,?,?,?)", (_token_hash(token), user_id, profile_id, 1, now, now))


def create_profile(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id); profile_id, token, now = _id(), secrets.token_urlsafe(32), _now()
    kernel = str(data.get("targetKernel") or "mihomo")
    if kernel not in {"mihomo", "clash"}: raise ToolboxError("INVALID_KERNEL", "目标内核必须是 mihomo 或 clash。", status_code=422)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("INSERT INTO csm_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)", (profile_id, str(data.get("name") or "未命名聚合")[:120], kernel, _json(data.get("settings") or {}), data.get("ruleSetId"), _encrypt(token), None, "draft", "[]", now, now))
        row = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
    _create_token_index(user.id, profile_id, token)
    return _profile_row(row, token)


def update_profile(profile_id: str, data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    allowed = {"name": "name", "targetKernel": "target_kernel", "ruleSetId": "rule_set_id"}
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row: _not_found("聚合配置")
        changed: dict[str, Any] = {}
        for inbound, column in allowed.items():
            if inbound in data: changed[column] = data[inbound]
        if "targetKernel" in data and data["targetKernel"] not in {"mihomo", "clash"}: raise ToolboxError("INVALID_KERNEL", "目标内核无效。", status_code=422)
        if "settings" in data: changed["settings_json"] = _json(data["settings"] or {})
        if changed:
            sql = ",".join(f"{key}=?" for key in changed)
            conn.execute(f"UPDATE csm_profiles SET {sql},updated_at=? WHERE id=?", (*changed.values(), _now(), profile_id))
        return _profile_row(conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone())


def delete_profile(profile_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_profiles WHERE id=?", (profile_id,)).rowcount == 0: _not_found("聚合配置")
    with connection_context() as conn: conn.execute("DELETE FROM csm_public_tokens WHERE user_id=? AND profile_id=?", (user.id, profile_id))


def set_profile_selections(profile_id: str, identities: list[str], user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if not conn.execute("SELECT 1 FROM csm_profiles WHERE id=?", (profile_id,)).fetchone(): _not_found("聚合配置")
        conn.execute("DELETE FROM csm_profile_selections WHERE profile_id=?", (profile_id,))
        now = _now(); conn.executemany("INSERT INTO csm_profile_selections VALUES(?,?,?)", [(profile_id, str(value), now) for value in dict.fromkeys(identities)][:5000])
    return {"profileId": profile_id, "selected": len(dict.fromkeys(identities))}


def _rule_row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "rules": _loads(row["rules_json"], []), "providers": _loads(row["providers_json"], {}), "groups": _loads(row["groups_json"], []), "importMeta": _loads(row["import_meta_json"], {}), "updatedAt": row["updated_at"]}


def list_rule_sets(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_rule_row(row) for row in conn.execute("SELECT * FROM csm_rule_sets ORDER BY updated_at DESC")]


def _normalise_rules(data: dict[str, Any]) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]]]:
    rules = data.get("rules") or []
    providers = data.get("providers") or data.get("ruleProviders") or {}
    groups = data.get("groups") or data.get("proxyGroups") or []
    if not isinstance(rules, list) or not isinstance(providers, dict) or not isinstance(groups, list):
        raise ToolboxError("INVALID_RULE_SET", "规则、规则提供器和策略组必须分别为数组、对象和数组。", status_code=422)
    clean_groups = [dict(item) for item in groups if isinstance(item, dict) and item.get("name")]
    return rules[:5000], providers, clean_groups[:1000]


def save_rule_set(data: dict[str, Any], user: User, rule_set_id: str | None = None) -> dict[str, Any]:
    init_database(user.id); rules, providers, groups = _normalise_rules(data); now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if rule_set_id:
            row = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (rule_set_id,)).fetchone()
            if not row: _not_found("规则库")
            conn.execute("UPDATE csm_rule_sets SET name=?,rules_json=?,providers_json=?,groups_json=?,updated_at=? WHERE id=?", (str(data.get("name") or row["name"])[:120], _json(rules), _json(providers), _json(groups), now, rule_set_id))
        else:
            rule_set_id = _id()
            conn.execute("INSERT INTO csm_rule_sets VALUES(?,?,?,?,?,?,?,?)", (rule_set_id, str(data.get("name") or "规则库")[:120], _json(rules), _json(providers), _json(groups), _json(data.get("importMeta") or {}), now, now))
        return _rule_row(conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (rule_set_id,)).fetchone())


def import_rule_set(data: dict[str, Any], user: User) -> dict[str, Any]:
    """Import is always a detached editable copy; it never updates a source."""
    content = str(data.get("content") or "")
    if not content and data.get("url"): content = _download(_require_http_url(str(data["url"]))).decode("utf-8-sig", "replace")
    try: document = yaml.safe_load(content)
    except yaml.YAMLError as exc: raise ToolboxError("INVALID_RULE_YAML", "规则 YAML 无法解析。", status_code=422) from exc
    if not isinstance(document, dict): raise ToolboxError("INVALID_RULE_YAML", "规则内容必须是 YAML 对象。", status_code=422)
    return save_rule_set({"name": data.get("name") or "导入规则", "rules": document.get("rules", []), "providers": document.get("rule-providers", {}), "groups": document.get("proxy-groups", []), "importMeta": {"importedAt": _now(), "source": "url" if data.get("url") else "paste"}}, user)


def delete_rule_set(rule_set_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_rule_sets WHERE id=?", (rule_set_id,)).rowcount == 0: _not_found("规则库")


def _profile_material(conn: sqlite3.Connection, profile_id: str) -> tuple[sqlite3.Row, list[sqlite3.Row], dict[str, Any] | None, list[str]]:
    profile = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile: _not_found("聚合配置")
    selected = [r["node_identity"] for r in conn.execute("SELECT node_identity FROM csm_profile_selections WHERE profile_id=?", (profile_id,))]
    nodes: list[sqlite3.Row] = []
    missing: list[str] = []
    for identity in selected:
        row = conn.execute("SELECT * FROM csm_nodes WHERE stable_identity=?", (identity,)).fetchone()
        if row: nodes.append(row)
        else: missing.append(identity)
    rule = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (profile["rule_set_id"],)).fetchone() if profile["rule_set_id"] else None
    return profile, nodes, _rule_row(rule) if rule else None, missing


def _rule_target(rule: Any) -> str:
    if isinstance(rule, str): return rule.rsplit(",", 1)[-1].strip()
    if isinstance(rule, dict): return str(rule.get("target") or rule.get("policy") or rule.get("proxy") or "")
    return ""


def _validate_material(profile: sqlite3.Row, nodes: list[sqlite3.Row], rule_set: dict[str, Any] | None, missing: list[str]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    kernel = profile["target_kernel"]
    for item in nodes:
        if not bool(item["supported_output"]): messages.append({"level": "error", "code": "UNSUPPORTED_NODE", "message": f"节点 {item['name']} 的协议无法安全输出。"})
        elif not _compatible(item["protocol"], kernel): messages.append({"level": "error", "code": "KERNEL_INCOMPATIBLE", "message": f"节点 {item['name']}（{item['protocol']}）不兼容传统 Clash。"})
    for identity in missing: messages.append({"level": "error", "code": "MISSING_SELECTION", "message": f"已选节点 {identity[:12]}… 已从节点池消失。"})
    if not rule_set: return messages
    groups = rule_set["groups"]; names = {str(group.get("name")) for group in groups if group.get("name")}
    node_names = {node["name"] for node in nodes}
    graph: dict[str, set[str]] = {}
    for group in groups:
        name = str(group.get("name") or "")
        if not name: continue
        refs = group.get("proxies") or group.get("use") or []
        if not isinstance(refs, list):
            messages.append({"level": "error", "code": "INVALID_GROUP", "message": f"策略组 {name} 的成员必须是列表。"}); continue
        graph[name] = {str(ref) for ref in refs if str(ref) in names}
        for ref in refs:
            if str(ref) not in names and str(ref) not in node_names and str(ref) not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
                messages.append({"level": "error", "code": "MISSING_GROUP_REFERENCE", "message": f"策略组 {name} 引用了不存在的节点或子组：{ref}。"})
    visiting, visited = set(), set()
    def visit(name: str) -> None:
        if name in visiting: messages.append({"level": "error", "code": "GROUP_CYCLE", "message": f"策略组存在循环引用：{name}。"}); return
        if name in visited: return
        visiting.add(name)
        for child in graph.get(name, set()): visit(child)
        visiting.remove(name); visited.add(name)
    for name in graph: visit(name)
    for rule in rule_set["rules"]:
        target = _rule_target(rule)
        if target and target not in names and target not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
            messages.append({"level": "error", "code": "MISSING_RULE_TARGET", "message": f"规则引用不存在的策略组：{target}。"})
    for index, rule in enumerate(rule_set["rules"]):
        value = rule if isinstance(rule, str) else str(rule.get("type") or "")
        if str(value).upper().startswith("MATCH") and index != len(rule_set["rules"]) - 1:
            messages.append({"level": "error", "code": "MATCH_POSITION", "message": "MATCH 终止规则必须位于规则列表末尾。"})
    for name, provider in rule_set["providers"].items():
        if not isinstance(provider, dict) or not provider.get("type") or not (provider.get("url") or provider.get("path")):
            messages.append({"level": "error", "code": "INVALID_PROVIDER", "message": f"Rule Provider {name} 缺少 type、url 或 path。"})
    return messages


def validate_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile, nodes, rule_set, missing = _profile_material(conn, profile_id)
        messages = _validate_material(profile, nodes, rule_set, missing)
        conn.execute("UPDATE csm_profiles SET last_validation_json=?,updated_at=? WHERE id=?", (_json(messages), _now(), profile_id))
    return {"profileId": profile_id, "valid": not any(m["level"] == "error" for m in messages), "messages": messages, "selectedNodeCount": len(nodes), "missingSelections": missing}


def _unique_proxies(nodes: list[sqlite3.Row]) -> list[dict[str, Any]]:
    used: dict[str, int] = {}; output = []
    for row in nodes:
        value = _loads(row["config_json"], {}); base = str(value.get("name") or row["name"]); count = used.get(base, 0); used[base] = count + 1
        if count: value["name"] = f"{base} ({count + 1})"
        output.append(value)
    return output


def _build_config(profile: sqlite3.Row, nodes: list[sqlite3.Row], rule_set: dict[str, Any] | None) -> dict[str, Any]:
    settings = _loads(profile["settings_json"], {})
    config: dict[str, Any] = {"mixed-port": int(settings.get("mixedPort", 7890)), "allow-lan": bool(settings.get("allowLan", False)), "mode": settings.get("mode", "rule"), "ipv6": bool(settings.get("ipv6", False)), "proxies": _unique_proxies(nodes)}
    if settings.get("dns") is not None: config["dns"] = settings["dns"]
    if rule_set:
        config["proxy-groups"] = rule_set["groups"]
        config["rules"] = rule_set["rules"]
        if rule_set["providers"]: config["rule-providers"] = rule_set["providers"]
    return config


def preview_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile, nodes, rule_set, missing = _profile_material(conn, profile_id)
        messages = _validate_material(profile, nodes, rule_set, missing)
        content = yaml.safe_dump(_build_config(profile, nodes, rule_set), allow_unicode=True, sort_keys=False)
    return {"yaml": content, "valid": not any(m["level"] == "error" for m in messages), "messages": messages}


def publish_profile(profile_id: str, user: User) -> dict[str, Any]:
    preview = preview_profile(profile_id, user)
    if not preview["valid"]:
        raise ToolboxError("PUBLISH_VALIDATION_FAILED", "配置校验未通过，已发布版本未被覆盖。", status_code=422, extra={"messages": preview["messages"]})
    init_database(user.id); now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
        _create_published_snapshot(conn, profile_id, preview["yaml"], now)
        conn.execute("UPDATE csm_profiles SET published_at=?,published_status='published',last_validation_json=?,updated_at=? WHERE id=?", (now, _json(preview["messages"]), now, profile_id))
        token = _decrypt(profile["token_encrypted"])
    return {"profileId": profile_id, "publishedAt": now, "subscriptionToken": token, "yaml": preview["yaml"]}


def _create_published_snapshot(conn: sqlite3.Connection, profile_id: str, content: str, now: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS csm_published_snapshots (id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, content_encrypted TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO csm_published_snapshots VALUES(?,?,?,?,?)", (_id(), profile_id, _encrypt(content), hashlib.sha256(content.encode()).hexdigest(), now))


def rotate_profile_token(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id); token = secrets.token_urlsafe(32); now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if not conn.execute("SELECT 1 FROM csm_profiles WHERE id=?", (profile_id,)).fetchone(): _not_found("聚合配置")
        conn.execute("UPDATE csm_profiles SET token_encrypted=?,updated_at=? WHERE id=?", (_encrypt(token), now, profile_id))
    with connection_context() as conn:
        conn.execute("UPDATE csm_public_tokens SET enabled=0,updated_at=? WHERE user_id=? AND profile_id=?", (now, user.id, profile_id))
        conn.execute("INSERT INTO csm_public_tokens VALUES(?,?,?,?,?,?)", (_token_hash(token), user.id, profile_id, 1, now, now))
    return {"profileId": profile_id, "subscriptionToken": token}


def public_subscription(token: str) -> tuple[str, str, str, str] | None:
    """Return last successful publication without exposing why a lookup fails."""
    if not token or len(token) > 300: return None
    with connection_context() as conn:
        index = conn.execute("SELECT user_id,profile_id FROM csm_public_tokens WHERE token_hash=? AND enabled=1", (_token_hash(token),)).fetchone()
    if not index: return None
    try:
        init_database(index["user_id"])
        with user_tool_connection_context(index["user_id"], TOOL_ID) as conn:
            profile = conn.execute("SELECT name FROM csm_profiles WHERE id=?", (index["profile_id"],)).fetchone()
            snapshot = conn.execute("SELECT * FROM csm_published_snapshots WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (index["profile_id"],)).fetchone()
            if not profile or not snapshot: return None
            return _decrypt(snapshot["content_encrypted"]), snapshot["content_hash"], snapshot["created_at"], profile["name"]
    except (sqlite3.Error, ToolboxError): return None


def _probe_one(node: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic(); host, port = node["server"], node["port"]
    if not host or not port: return {"node_id": node["id"], "reachable": False, "dns_address": "", "latency_ms": None, "error": "缺少服务器或端口"}
    try:
        infos = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
        address = infos[0][4][0]
        with socket.create_connection((address, int(port)), timeout=3): pass
        return {"node_id": node["id"], "reachable": True, "dns_address": address, "latency_ms": round((time.monotonic()-started)*1000, 1), "error": ""}
    except (OSError, ValueError) as exc:
        return {"node_id": node["id"], "reachable": False, "dns_address": "", "latency_ms": None, "error": str(exc)[:240]}


def probe_nodes(node_ids: list[str], user: User) -> list[dict[str, Any]]:
    init_database(user.id); ids = list(dict.fromkeys(str(value) for value in node_ids))[:1000]
    if not ids: return []
    marks = ",".join("?" for _ in ids)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        nodes = [_node_dict(row) for row in conn.execute(f"SELECT * FROM csm_nodes WHERE id IN ({marks})", ids)]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(20, len(nodes))) as executor:
        futures = [executor.submit(_probe_one, node) for node in nodes]
        for future in as_completed(futures): results.append(future.result())
    now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.executemany("INSERT INTO csm_probe_results VALUES(?,?,?,?,?,?,?)", [(_id(), value["node_id"], int(value["reachable"]), value["dns_address"], value["latency_ms"], value["error"], now) for value in results])
    return [{"nodeId": result["node_id"], "reachable": result["reachable"], "dnsAddress": result["dns_address"], "latencyMs": result["latency_ms"], "error": result["error"], "checkedAt": now} for result in results]


def dashboard(user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        source_counts = conn.execute("SELECT COUNT(*) AS total,SUM(status='healthy') AS healthy,SUM(status='error') AS error FROM csm_sources").fetchone()
        node_count = int(conn.execute("SELECT COUNT(*) FROM csm_nodes").fetchone()[0])
        source_node_count = int(conn.execute("SELECT COUNT(*) FROM csm_node_sources").fetchone()[0])
        tcp_count = int(conn.execute("SELECT COUNT(DISTINCT node_id) FROM csm_probe_results WHERE reachable=1").fetchone()[0])
        published = int(conn.execute("SELECT COUNT(*) FROM csm_profiles WHERE published_at IS NOT NULL").fetchone()[0])
        protocol = [{"name": r["protocol"], "value": r["count"]} for r in conn.execute("SELECT protocol,COUNT(*) count FROM csm_nodes GROUP BY protocol ORDER BY count DESC")]
        sources = [_row_source(r) for r in conn.execute("SELECT * FROM csm_sources ORDER BY CASE status WHEN 'error' THEN 0 ELSE 1 END,last_attempt_at DESC LIMIT 12")]
        profiles = [_profile_row(r) for r in conn.execute("SELECT * FROM csm_profiles ORDER BY updated_at DESC LIMIT 12")]
        alerts = []
        for source in sources:
            if source["status"] == "error": alerts.append({"kind": "source", "level": "error", "message": f"订阅源 {source['name']} 刷新失败：{source['lastError']}"})
        for profile in profiles:
            for message in profile["validation"]:
                if message.get("level") == "error": alerts.append({"kind": "profile", "level": "error", "profileId": profile["id"], "message": message.get("message", "配置校验异常")})
    return {"metrics": {"sources": int(source_counts["total"] or 0), "healthySources": int(source_counts["healthy"] or 0), "errorSources": int(source_counts["error"] or 0), "nodesBeforeDedupe": source_node_count, "nodes": node_count, "tcpReachable": tcp_count, "publishedProfiles": published, "alerts": len(alerts)}, "protocolDistribution": protocol, "sources": sources, "profiles": profiles, "alerts": alerts[:30]}


def refresh_runs(user: User, limit: int = 100) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute("SELECT r.*,s.name AS source_name FROM csm_refresh_runs r JOIN csm_sources s ON s.id=r.source_id ORDER BY r.created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [{"id": row["id"], "sourceId": row["source_id"], "sourceName": row["source_name"], "status": row["status"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "durationMs": row["duration_ms"], "nodesBefore": row["nodes_before"], "nodesAfter": row["nodes_after"], "error": row["error"]} for row in rows]


def _rebuild_published_profiles(user: User) -> None:
    """Publish only a complete valid replacement. Invalid rebuilds retain old YAML."""
    for profile in list_profiles(user):
        if not profile["publishedAt"]: continue
        try: publish_profile(profile["id"], user)
        except ToolboxError:
            with user_tool_connection_context(user.id, TOOL_ID) as conn:
                conn.execute("UPDATE csm_profiles SET published_status='degraded',updated_at=? WHERE id=?", (_now(), profile["id"]))


def refresh_due_sources() -> None:
    """Scheduler entry: per-user databases are discovered without user sessions."""
    now = _now()
    for user_id, _path in list_user_tool_dbs(TOOL_ID):
        init_database(user_id)
        # The service only needs the id, but constructing the normal user object
        # is unnecessary and would couple scheduled work to authentication data.
        class ScheduledUser:  # noqa: D101
            id = user_id
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            due = [r["id"] for r in conn.execute("SELECT id FROM csm_sources WHERE enabled=1 AND (next_refresh_at IS NULL OR next_refresh_at<=?)", (now,))]
        for source_id in due:
            try: refresh_source(source_id, ScheduledUser(), auto_rebuild=True)  # type: ignore[arg-type]
            except ToolboxError: pass
