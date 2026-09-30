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
import re
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
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_SECONDS = 0.35
PARSEABLE_URIS = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic"}
CLASH_STRUCTURED_TYPES = {"http", "socks5", "snell", "wireguard"}
CLASH_META_PROXY_TYPES = PARSEABLE_URIS | CLASH_STRUCTURED_TYPES
COMPATIBLE_GROUP_TYPES = {"select", "url-test", "fallback", "load-balance", "relay"}
PROXY_COMMON_FIELDS = {"name", "type", "server", "port"}
PROXY_FIELDS: dict[str, set[str]] = {
    "ss": {"cipher", "password", "udp", "plugin", "plugin-opts"},
    "ssr": {"cipher", "password", "protocol", "protocol-param", "obfs", "obfs-param", "udp"},
    "vmess": {"uuid", "alterId", "cipher", "udp", "tls", "skip-cert-verify", "servername", "network", "ws-opts", "http-opts", "h2-opts"},
    "trojan": {"password", "udp", "sni", "alpn", "skip-cert-verify", "client-fingerprint", "network", "ws-opts", "grpc-opts"},
    "vless": {"uuid", "udp", "tls", "flow", "servername", "sni", "network", "skip-cert-verify", "client-fingerprint", "reality-opts", "ws-opts", "grpc-opts", "packet-encoding"},
    "hysteria": {"auth", "auth-str", "protocol", "up", "down", "sni", "alpn", "skip-cert-verify", "obfs", "obfs-password", "recv-window-conn", "recv-window"},
    "hysteria2": {"password", "up", "down", "sni", "alpn", "skip-cert-verify", "obfs", "obfs-password", "ports", "hop-interval"},
    "tuic": {"uuid", "password", "ip", "sni", "alpn", "skip-cert-verify", "disable-sni", "reduce-rtt", "request-timeout", "udp-relay-mode", "congestion-controller", "heartbeat-interval", "max-udp-relay-packet-size"},
    "wireguard": {"ip", "ipv6", "private-key", "public-key", "pre-shared-key", "reserved", "udp", "mtu", "dns", "remote-dns", "allowed-ips"},
    "http": {"username", "password", "tls", "skip-cert-verify", "sni"},
    "socks5": {"username", "password", "tls", "skip-cert-verify", "udp"},
    "snell": {"psk", "version", "obfs-opts"},
}
GROUP_FIELDS = {"name", "type", "proxies", "url", "interval", "lazy", "disable-udp", "strategy"}
DNS_FIELDS = {"enable", "ipv6", "listen", "enhanced-mode", "fake-ip-range", "use-hosts", "nameserver", "fallback", "fallback-filter", "default-nameserver", "nameserver-policy", "fake-ip-filter", "proxy-server-nameserver", "respect-rules", "prefer-h3"}
RULE_PROVIDER_FIELDS = {"type", "behavior", "url", "path", "interval"}
RULE_PROVIDER_OUTPUT_MODES = {"url", "inline"}
RULE_PROVIDER_OUTPUT_MODE_DEFAULT = "inline"
CLASH_META_RULE_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR", "GEOIP", "GEOSITE", "DST-PORT", "SRC-PORT", "PROCESS-NAME", "PROCESS-PATH", "RULE-SET", "MATCH"}
BUILTIN_RULE_PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": "builtin-provider-ai", "name": "AI 平台", "providerKey": "ai-platforms", "description": "OpenAI、Claude、Gemini 等常见生成式 AI 平台。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ai-!cn.yaml", "path": "./ruleset/ai-platforms.yaml"}},
    {"id": "builtin-provider-google", "name": "谷歌平台", "providerKey": "google", "description": "Google、YouTube、Gmail 及相关服务。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/google.yaml", "path": "./ruleset/google.yaml"}},
    {"id": "builtin-provider-overseas", "name": "常见国外平台", "providerKey": "common-overseas", "description": "常见非中国大陆互联网平台域名集合。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/geolocation-!cn.yaml", "path": "./ruleset/common-overseas.yaml"}},
    {"id": "builtin-provider-github", "name": "GitHub", "providerKey": "github", "description": "GitHub 及其静态资源、代码托管相关域名。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/github.yaml", "path": "./ruleset/github.yaml"}},
    {"id": "builtin-provider-media", "name": "海外影音娱乐", "providerKey": "overseas-media", "description": "常见海外流媒体、直播与影音平台。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-entertainment.yaml", "path": "./ruleset/overseas-media.yaml"}},
)
_initialized: set[str] = set()
_init_lock = threading.Lock()

register_tool_categories(TOOL_ID, [
    DataCategory("configuration", ["csm_sources", "csm_nodes", "csm_node_sources", "csm_profiles", "csm_rule_sets", "csm_rule_providers", "csm_node_groups", "csm_published_snapshots"], None, "订阅源、节点、配置和最后有效发布版本"),
    DataCategory("history", ["csm_source_snapshots", "csm_refresh_runs", "csm_probe_results", "csm_subscription_requests"], "created_at", "订阅刷新、快照、TCP 探测和聚合订阅请求记录"),
    DataCategory("public_tokens", ["csm_public_tokens"], None, "公开订阅令牌索引", storage="platform_db", user_id_column="user_id"),
])


NODE_GROUP_KINDS = {"custom", "region", "latency"}
# Node names rarely carry machine-readable country data, so region groups rely on
# a small heuristic over flags, common Chinese/English region names and 2-letter
# codes.  Order matters: longer/more-specific patterns come first.
COUNTRY_MATCHERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("HK", "香港", ("香港", "🇭🇰", "hong kong", "hongkong", "hk")),
    ("TW", "台湾", ("台湾", "台北", "🇹🇼", "taiwan", "taipei", "tw")),
    ("JP", "日本", ("日本", "东京", "大阪", "🇯🇵", "japan", "tokyo", "osaka", "jp")),
    ("KR", "韩国", ("韩国", "首尔", "🇰🇷", "korea", "seoul", "kr")),
    ("SG", "新加坡", ("新加坡", "🇸🇬", "singapore", "sg")),
    ("US", "美国", ("美国", "洛杉矶", "圣何塞", "纽约", "🇺🇸", "united states", "usa", "los angeles", "new york", "us")),
    ("GB", "英国", ("英国", "伦敦", "🇬🇧", "united kingdom", "britain", "london", "uk", "gb")),
    ("DE", "德国", ("德国", "法兰克福", "🇩🇪", "germany", "frankfurt", "de")),
    ("FR", "法国", ("法国", "巴黎", "🇫🇷", "france", "paris", "fr")),
    ("NL", "荷兰", ("荷兰", "阿姆斯特丹", "🇳🇱", "netherlands", "amsterdam", "nl")),
    ("CA", "加拿大", ("加拿大", "🇨🇦", "canada", "toronto", "vancouver", "ca")),
    ("AU", "澳大利亚", ("澳大利亚", "澳洲", "悉尼", "🇦🇺", "australia", "sydney", "au")),
    ("RU", "俄罗斯", ("俄罗斯", "莫斯科", "🇷🇺", "russia", "moscow", "ru")),
    ("IN", "印度", ("印度", "孟买", "🇮🇳", "india", "mumbai", "in")),
    ("TR", "土耳其", ("土耳其", "🇹🇷", "turkey", "tr")),
    ("BR", "巴西", ("巴西", "🇧🇷", "brazil", "br")),
    ("AR", "阿根廷", ("阿根廷", "🇦🇷", "argentina", "ar")),
    ("CN", "中国", ("中国", "大陆", "🇨🇳", "china", "cn")),
    ("TH", "泰国", ("泰国", "🇹🇭", "thailand", "bangkok", "th")),
    ("MY", "马来西亚", ("马来西亚", "🇲🇾", "malaysia", "my")),
    ("VN", "越南", ("越南", "🇻🇳", "vietnam", "vn")),
    ("ID", "印度尼西亚", ("印度尼西亚", "印尼", "🇮🇩", "indonesia", "id")),
    ("PH", "菲律宾", ("菲律宾", "🇵🇭", "philippines", "ph")),
    ("CH", "瑞士", ("瑞士", "🇨🇭", "switzerland", "zurich", "ch")),
    ("SE", "瑞典", ("瑞典", "🇸🇪", "sweden", "se")),
    ("ES", "西班牙", ("西班牙", "🇪🇸", "spain", "madrid", "es")),
    ("IT", "意大利", ("意大利", "🇮🇹", "italy", "milan", "it")),
    ("AE", "阿联酋", ("阿联酋", "迪拜", "🇦🇪", "united arab emirates", "dubai", "uae", "ae")),
)

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


def _seed_rule_providers(conn: sqlite3.Connection) -> None:
    now = _now()
    for item in BUILTIN_RULE_PROVIDERS:
        conn.execute("""INSERT OR IGNORE INTO csm_rule_providers(
          id,name,provider_key,description,config_json,is_builtin,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?)""", (item["id"], item["name"], item["providerKey"],
                                      item["description"], _json(item["config"]), 1, now, now))


def _migrate_rule_provider_snapshots(conn: sqlite3.Connection) -> None:
    """Move legacy rule-set Provider copies into the independent live library."""
    library = {row["provider_key"]: row["id"] for row in conn.execute(
        "SELECT id,provider_key FROM csm_rule_providers")}
    now = _now()
    for row in conn.execute("SELECT id,rules_json,providers_json,import_meta_json FROM csm_rule_sets"):
        snapshots = _loads(row["providers_json"], {})
        if not isinstance(snapshots, dict) or not snapshots:
            continue
        for raw_key, config in snapshots.items():
            key = str(raw_key).strip()
            if not key or key in library or not isinstance(config, dict):
                continue
            provider_id = _id()
            conn.execute("""INSERT INTO csm_rule_providers(
              id,name,provider_key,description,config_json,is_builtin,created_at,updated_at)
              VALUES(?,?,?,?,?,0,?,?)""", (provider_id, key, key, "从旧规则库迁移", _json(config), now, now))
            library[key] = provider_id
        meta = _loads(row["import_meta_json"], {})
        if not isinstance(meta, dict):
            meta = {}
        bindings = meta.get("providerBindings")
        if not isinstance(bindings, dict):
            bindings = {}
        for rule in _loads(row["rules_json"], []):
            if not isinstance(rule, str):
                continue
            parts = [part.strip() for part in rule.split(",")]
            if len(parts) < 3 or parts[0].upper() != "RULE-SET" or parts[1] not in library:
                continue
            ids = bindings.setdefault(parts[2], [])
            if isinstance(ids, list) and library[parts[1]] not in ids:
                ids.append(library[parts[1]])
        meta["providerBindings"] = bindings
        meta["strategyGroupEditor"] = True
        conn.execute("UPDATE csm_rule_sets SET providers_json='{}',import_meta_json=?,updated_at=? WHERE id=?",
                     (_json(meta), now, row["id"]))


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
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              alias TEXT NOT NULL DEFAULT '', is_custom INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS csm_node_sources (
              node_id TEXT NOT NULL, source_id TEXT NOT NULL, source_alias TEXT NOT NULL DEFAULT '',
              last_seen_at TEXT NOT NULL, PRIMARY KEY(node_id, source_id),
              FOREIGN KEY(node_id) REFERENCES csm_nodes(id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES csm_sources(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_profiles (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, target_kernel TEXT NOT NULL DEFAULT 'clash-meta',
              settings_json TEXT NOT NULL DEFAULT '{}', rule_set_id TEXT, token_encrypted TEXT NOT NULL,
              published_at TEXT, published_status TEXT NOT NULL DEFAULT 'draft', last_validation_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_rule_sets (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, rules_json TEXT NOT NULL DEFAULT '[]',
              providers_json TEXT NOT NULL DEFAULT '{}', groups_json TEXT NOT NULL DEFAULT '[]',
              import_meta_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              group_name TEXT NOT NULL DEFAULT '默认分组');
            CREATE TABLE IF NOT EXISTS csm_rule_providers (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, provider_key TEXT NOT NULL UNIQUE,
              description TEXT NOT NULL DEFAULT '', config_json TEXT NOT NULL DEFAULT '{}',
              is_builtin INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
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
            CREATE TABLE IF NOT EXISTS csm_node_groups (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
              config_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_subscription_requests (
              id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, requested_at TEXT NOT NULL,
              client_ip TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '',
              status_code INTEGER NOT NULL DEFAULT 200,
              FOREIGN KEY(profile_id) REFERENCES csm_profiles(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS csm_nodes_seen ON csm_nodes(last_seen_at);
            CREATE INDEX IF NOT EXISTS csm_runs_source ON csm_refresh_runs(source_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS csm_subscription_requests_profile ON csm_subscription_requests(profile_id, requested_at DESC);
            """)
            # Lightweight, idempotent migrations for databases created by an
            # earlier version of the tool.
            node_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_nodes)")}
            if "alias" not in node_columns:
                conn.execute("ALTER TABLE csm_nodes ADD COLUMN alias TEXT NOT NULL DEFAULT ''")
            if "is_custom" not in node_columns:
                conn.execute("ALTER TABLE csm_nodes ADD COLUMN is_custom INTEGER NOT NULL DEFAULT 0")
            rule_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_rule_sets)")}
            if "group_name" not in rule_columns:
                conn.execute("ALTER TABLE csm_rule_sets ADD COLUMN group_name TEXT NOT NULL DEFAULT '默认分组'")
            placeholders = ",".join("?" for _ in CLASH_META_PROXY_TYPES)
            conn.execute(f"UPDATE csm_nodes SET supported_output=CASE WHEN lower(protocol) IN ({placeholders}) THEN 1 ELSE 0 END", tuple(sorted(CLASH_META_PROXY_TYPES)))
            _seed_rule_providers(conn)
            _migrate_rule_provider_snapshots(conn)
            # Manual profile node selection was replaced by rule-driven output.
            conn.execute("DROP TABLE IF EXISTS csm_profile_selections")
            # A subscription now exposes only its current publication. Remove
            # legacy history once, then enforce one row per profile.
            conn.execute("""DELETE FROM csm_published_snapshots WHERE id IN (
              SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                  PARTITION BY profile_id ORDER BY created_at DESC, rowid DESC
                ) AS position
                FROM csm_published_snapshots
              ) WHERE position <> 1
            )""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS csm_published_snapshots_profile ON csm_published_snapshots(profile_id)")
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
        if not conn.execute("SELECT id FROM csm_sources WHERE id=?", (source_id,)).fetchone():
            _not_found("订阅源")
        conn.execute("DELETE FROM csm_source_snapshots WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM csm_refresh_runs WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM csm_node_sources WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM csm_sources WHERE id=?", (source_id,))
        conn.execute("""DELETE FROM csm_nodes WHERE is_custom=0 AND NOT EXISTS(
          SELECT 1 FROM csm_node_sources WHERE csm_node_sources.node_id=csm_nodes.id)""")


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
            "server": server, "port": port, "config": config, "supported_output": supported and protocol in CLASH_META_PROXY_TYPES}


def parse_uri(uri: str) -> dict[str, Any] | None:
    """Parse a supported share URI without silently inventing fields."""
    raw = uri.strip()
    if not raw or "://" not in raw: return None
    kind = raw.split("://", 1)[0].lower()
    if kind not in PARSEABLE_URIS: return None
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
    if kind in {"hysteria", "hysteria2"}:
        config.update({"obfs": _query_one(parsed.query, "obfs"), "obfs-password": _query_one(parsed.query, "obfs-password"), "up": _query_one(parsed.query, "up"), "down": _query_one(parsed.query, "down")})
    if kind == "hysteria":
        config["auth-str"] = credentials
    if kind == "hysteria2":
        config["password"] = unquote(parsed.password or "") or credentials
    if kind == "tuic":
        config.update({"uuid": credentials, "password": unquote(parsed.password or ""), "congestion-controller": _query_one(parsed.query, "congestion_control")})
    if kind == "vless":
        config.update({
            "uuid": credentials,
            "client-fingerprint": _query_one(parsed.query, "fp"),
            "packet-encoding": _query_one(parsed.query, "packetEncoding"),
            "reality-opts": {"public-key": _query_one(parsed.query, "pbk"), "short-id": _query_one(parsed.query, "sid")},
            "ws-opts": {"path": _query_one(parsed.query, "path"), "headers": {"Host": _query_one(parsed.query, "host")}},
            "grpc-opts": {"grpc-service-name": _query_one(parsed.query, "serviceName")},
        })
    return _node(name, kind, server, port, config)


def _clash_node(value: dict[str, Any]) -> dict[str, Any] | None:
    protocol = str(value.get("type") or "").lower()
    if not protocol: return None
    server = str(value.get("server") or "")
    try: port = int(value.get("port")) if value.get("port") is not None else None
    except (ValueError, TypeError): port = None
    supported = protocol in CLASH_META_PROXY_TYPES
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


def _download_error_detail(exc: httpx.HTTPError) -> str:
    """Describe a fetch failure without exposing a token-bearing URL."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        reason = exc.response.reason_phrase.strip()
        return f"HTTP {status}" + (f" {reason}" if reason else "")
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    # Transport messages contain useful DNS/TLS diagnostics, but some httpx
    # errors append the request URL. Never persist the query string because
    # subscription credentials commonly live there.
    detail = str(exc).strip() or exc.__class__.__name__
    try:
        request_url = str(exc.request.url)
    except RuntimeError:
        request_url = ""
    if request_url:
        detail = detail.replace(request_url, _redact_url(request_url))
    return detail[:240]


def _retryable_download_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or exc.response.status_code >= 500
    return False


def _download(url: str, user_agent: str = "") -> bytes:
    headers = {"User-Agent": user_agent or "pansis-clash-subscription-manager/1.0"}
    last_error: httpx.HTTPError | None = None
    attempts_made = 0
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, max_redirects=MAX_REDIRECTS, headers=headers) as client:
            for attempt in range(DOWNLOAD_ATTEMPTS):
                attempts_made = attempt + 1
                try:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        chunks: list[bytes] = []; size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ToolboxError("SOURCE_TOO_LARGE", "订阅响应超过 10 MiB 限制。", status_code=422)
                            chunks.append(chunk)
                        return b"".join(chunks)
                except ToolboxError:
                    raise
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt + 1 >= DOWNLOAD_ATTEMPTS or not _retryable_download_error(exc):
                        break
                    time.sleep(DOWNLOAD_RETRY_SECONDS * (2 ** attempt))
    except ToolboxError:
        raise
    except httpx.HTTPError as exc:
        last_error = exc

    assert last_error is not None
    detail = _download_error_detail(last_error)
    attempt_note = f"（已尝试 {attempts_made} 次）" if attempts_made > 1 else ""
    raise ToolboxError("SOURCE_FETCH_FAILED", f"获取订阅失败{attempt_note}：{detail}", status_code=422) from last_error


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
                conn.execute("""INSERT INTO csm_nodes(
                  id,stable_identity,fingerprint,name,protocol,server,port,config_json,
                  supported_output,first_seen_at,last_seen_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (node_id, item["stable_identity"], item["fingerprint"], item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]), int(item["supported_output"]), now, now))
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


def _country_of(name: str) -> tuple[str | None, str | None]:
    """Best-effort country detection from a node display name."""
    text = str(name or "").strip()
    if not text:
        return None, None
    lowered = text.lower()
    for code, label, patterns in COUNTRY_MATCHERS:
        for pattern in patterns:
            if pattern.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", lowered):
                    return code, label
            elif pattern in text:
                return code, label
    return None, None


def _node_dict(row: sqlite3.Row, source_names: list[str] | None = None, probe: sqlite3.Row | None = None,
               source_ids: list[str] | None = None) -> dict[str, Any]:
    alias = str(row["alias"] or "")
    country, country_label = _country_of(row["name"])
    return {"id": row["id"], "stableIdentity": row["stable_identity"], "name": row["name"], "alias": alias,
            "displayName": alias or row["name"], "protocol": row["protocol"], "server": row["server"], "port": row["port"],
            "config": _loads(row["config_json"], {}), "supportedOutput": _compatible(str(row["protocol"])),
            "isCustom": bool(row["is_custom"]), "lastSeenAt": row["last_seen_at"], "sources": source_names or [],
            "sourceIds": source_ids or [], "country": country, "countryLabel": country_label,
            "tcp": None if not probe else {
                "reachable": bool(probe["reachable"]), "latencyMs": probe["latency_ms"], "error": probe["error"], "checkedAt": probe["created_at"]}}


def _normalise_custom_node(content: str) -> dict[str, Any]:
    """Validate one user-authored Clash Meta proxy mapping."""
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ToolboxError("INVALID_CUSTOM_NODE_YAML", "自定义节点 YAML 无法解析。", status_code=422) from exc
    if not isinstance(value, dict):
        raise ToolboxError("INVALID_CUSTOM_NODE", "自定义节点必须是一个 YAML 对象。", status_code=422)
    name = str(value.get("name") or "").strip()[:120]
    protocol = str(value.get("type") or "").strip().lower()
    server = str(value.get("server") or "").strip()
    if not name:
        raise ToolboxError("CUSTOM_NODE_NAME_REQUIRED", "请输入自定义节点名称。", status_code=422)
    if protocol not in CLASH_META_PROXY_TYPES:
        raise ToolboxError("INVALID_CUSTOM_NODE_TYPE", "节点协议不受 Clash Meta 支持。", status_code=422)
    if not server:
        raise ToolboxError("CUSTOM_NODE_SERVER_REQUIRED", "请输入自定义节点服务器地址。", status_code=422)
    raw_port = value.get("port")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ToolboxError("INVALID_CUSTOM_NODE_PORT", "节点端口必须是 1 到 65535 的整数。", status_code=422) from exc
    if isinstance(raw_port, bool) or not 1 <= port <= 65535:
        raise ToolboxError("INVALID_CUSTOM_NODE_PORT", "节点端口必须是 1 到 65535 的整数。", status_code=422)
    config = dict(value)
    config.update({"name": name, "type": protocol, "server": server, "port": port})
    return {"name": name, "protocol": protocol, "server": server, "port": port, "config": config}


def _insert_custom_node(conn: sqlite3.Connection, material: dict[str, Any], *, node_id: str | None = None) -> sqlite3.Row:
    node_id = node_id or _id()
    now = _now()
    stable_identity = _hash({"customNodeId": node_id})
    fingerprint = _hash({"customNodeId": node_id, "config": material["config"]})
    conn.execute("""INSERT INTO csm_nodes(
      id,stable_identity,fingerprint,name,protocol,server,port,config_json,
      supported_output,first_seen_at,last_seen_at,alias,is_custom)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""", (
        node_id, stable_identity, fingerprint, material["name"], material["protocol"],
        material["server"], material["port"], _json(material["config"]),
        int(_compatible(material["protocol"])), now, now, ""))
    return conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone()


def _clean_node_alias(conn: sqlite3.Connection, node_id: str, node_name: str, alias: str) -> str:
    clean = alias.strip()[:120]
    if clean == node_name:
        return ""
    if clean:
        conflict = conn.execute("SELECT id FROM csm_nodes WHERE id<>? AND (name=? OR alias=?) LIMIT 1", (node_id, clean, clean)).fetchone()
        if conflict:
            raise ToolboxError("NODE_ALIAS_CONFLICT", "该别名已被其他节点名称或别名占用。", status_code=409)
    return clean


def create_custom_node(content: str, user: User, alias: str = "") -> dict[str, Any]:
    init_database(user.id)
    material = _normalise_custom_node(content)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = _insert_custom_node(conn, material)
        clean_alias = _clean_node_alias(conn, row["id"], material["name"], alias)
        if clean_alias:
            conn.execute("UPDATE csm_nodes SET alias=? WHERE id=?", (clean_alias, row["id"]))
            row = conn.execute("SELECT * FROM csm_nodes WHERE id=?", (row["id"],)).fetchone()
        return _node_dict(row)


def update_custom_node(node_id: str, content: str, user: User, alias: str | None = None) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            _not_found("节点")
        if not row["is_custom"]:
            raise ToolboxError("SUBSCRIPTION_NODE_READ_ONLY", "订阅节点不能直接编辑，请先复制为自定义节点。", status_code=409)
        material = _normalise_custom_node(content)
        clean_alias = row["alias"] if alias is None else _clean_node_alias(conn, node_id, material["name"], alias)
        fingerprint = _hash({"customNodeId": node_id, "config": material["config"]})
        conn.execute("""UPDATE csm_nodes SET fingerprint=?,name=?,protocol=?,server=?,port=?,
                      config_json=?,supported_output=1,last_seen_at=?,alias=? WHERE id=?""", (
            fingerprint, material["name"], material["protocol"], material["server"],
            material["port"], _json(material["config"]), _now(), clean_alias, node_id))
        return _node_dict(conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone())


def copy_subscription_node(node_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            _not_found("节点")
        if row["is_custom"]:
            raise ToolboxError("CUSTOM_NODE_COPY_NOT_REQUIRED", "该节点已经是可编辑的自定义节点。", status_code=409)
        if not _compatible(str(row["protocol"])):
            raise ToolboxError("INVALID_CUSTOM_NODE_TYPE", "该订阅节点的协议不受 Clash Meta 支持，不能复制为自定义节点。", status_code=422)
        base = str(row["alias"] or row["name"] or "自定义节点")
        names = {str(item["name"]) for item in conn.execute("SELECT name FROM csm_nodes")}
        copy_name = f"{base}（副本）"
        sequence = 2
        while copy_name in names:
            copy_name = f"{base}（副本 {sequence}）"
            sequence += 1
        config = _loads(row["config_json"], {})
        config["name"] = copy_name
        material = {"name": copy_name, "protocol": row["protocol"], "server": row["server"],
                    "port": row["port"], "config": config}
        return _node_dict(_insert_custom_node(conn, material))


def delete_custom_node(node_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT is_custom FROM csm_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            _not_found("节点")
        if not row["is_custom"]:
            raise ToolboxError("SUBSCRIPTION_NODE_READ_ONLY", "订阅节点由订阅源管理，不能单独删除。", status_code=409)
        conn.execute("DELETE FROM csm_nodes WHERE id=?", (node_id,))


def update_node_alias(node_id: str, alias: str, user: User) -> dict[str, Any]:
    """Set a user-owned display/output name while preserving the source name."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone()
        if not row: _not_found("节点")
        clean = _clean_node_alias(conn, node_id, row["name"], alias)
        conn.execute("UPDATE csm_nodes SET alias=? WHERE id=?", (clean, node_id))
        return _node_dict(conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone())


def list_nodes(user: User, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    init_database(user.id); filters = filters or {}; terms, values = [], []
    for key, column in (("protocol", "n.protocol"), ("sourceId", "ns.source_id")):
        if filters.get(key): terms.append(f"{column}=?"); values.append(str(filters[key]))
    if filters.get("search"):
        terms.append("(n.name LIKE ? OR n.alias LIKE ? OR n.server LIKE ?)"); values.extend([f"%{filters['search']}%", f"%{filters['search']}%", f"%{filters['search']}%"])
    where = " WHERE " + " AND ".join(terms) if terms else ""
    query = f"""SELECT DISTINCT n.*, (SELECT group_concat(s.name,'|') FROM csm_node_sources ns2 JOIN csm_sources s ON s.id=ns2.source_id WHERE ns2.node_id=n.id) AS source_names,
      (SELECT group_concat(ns3.source_id,'|') FROM csm_node_sources ns3 WHERE ns3.node_id=n.id) AS source_ids,
      (SELECT pr.id FROM csm_probe_results pr WHERE pr.node_id=n.id ORDER BY pr.created_at DESC LIMIT 1) AS probe_id FROM csm_nodes n LEFT JOIN csm_node_sources ns ON ns.node_id=n.id{where} ORDER BY n.last_seen_at DESC"""
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        result=[]
        for row in conn.execute(query, values):
            probe = conn.execute("SELECT * FROM csm_probe_results WHERE id=?", (row["probe_id"],)).fetchone() if row["probe_id"] else None
            result.append(_node_dict(
                row,
                str(row["source_names"] or "").split("|") if row["source_names"] else [],
                probe,
                str(row["source_ids"] or "").split("|") if row["source_ids"] else [],
            ))
        return result


def _node_ref(row: sqlite3.Row) -> str:
    return str(row["alias"] or row["name"] or "")


def _group_scope_rows(conn: sqlite3.Connection, config: dict[str, Any]) -> list[sqlite3.Row]:
    """Nodes within a group's configured explicit node selection (empty means all nodes)."""
    node_ids = [str(item) for item in (config.get("nodeIds") or []) if item]
    if not node_ids:
        return list(conn.execute("SELECT * FROM csm_nodes").fetchall())
    placeholders = ",".join("?" for _ in node_ids)
    rows = conn.execute(f"SELECT * FROM csm_nodes WHERE id IN ({placeholders})", node_ids).fetchall()
    rows_by_id = {row["id"]: row for row in rows}
    return [rows_by_id[node_id] for node_id in node_ids if node_id in rows_by_id]


def _resolve_group_members(conn: sqlite3.Connection, group: sqlite3.Row) -> list[str]:
    kind = str(group["kind"])
    config = _loads(group["config_json"], {})
    if kind == "custom":
        node_ids = [str(item) for item in (config.get("nodeIds") or []) if item]
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = conn.execute(f"SELECT * FROM csm_nodes WHERE id IN ({placeholders})", node_ids).fetchall()
        rows_by_id = {row["id"]: row for row in rows}
        return [_node_ref(rows_by_id[node_id]) for node_id in node_ids if node_id in rows_by_id]
    rows = _group_scope_rows(conn, config)
    if kind == "region":
        countries = {str(item) for item in (config.get("countries") or []) if item}
        if not countries:
            return []
        return [_node_ref(row) for row in rows if (_country_of(row["name"])[0] or "") in countries]
    if kind == "latency":
        mode = str(config.get("mode") or "top")
        count = max(1, int(config.get("count") or 5))
        threshold_ms = max(0, int(config.get("thresholdMs") or 0))
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            probe = conn.execute("SELECT * FROM csm_probe_results WHERE node_id=? AND reachable=1 ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
            if probe and probe["latency_ms"] is not None:
                scored.append((int(probe["latency_ms"]), row))
        scored.sort(key=lambda item: item[0])
        if mode == "threshold":
            return [_node_ref(row) for latency, row in scored if latency <= threshold_ms]
        return [_node_ref(row) for _, row in scored[:count]]
    return []


def _node_group_row(row: sqlite3.Row, members: list[str]) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "config": _loads(row["config_json"], {}), "members": members,
            "memberCount": len(members), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def list_node_groups(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        result = []
        for row in conn.execute("SELECT * FROM csm_node_groups ORDER BY created_at DESC"):
            result.append(_node_group_row(row, _resolve_group_members(conn, row)))
        return result


def _normalise_node_group(data: dict[str, Any], user: User) -> tuple[str, str, dict[str, Any]]:
    name = str(data.get("name") or "").strip()[:120]
    if not name:
        raise ToolboxError("NODE_GROUP_NAME_REQUIRED", "请输入分组名称。", status_code=422)
    if any(char in name for char in ",\r\n"):
        raise ToolboxError("INVALID_NODE_GROUP_NAME", "分组名称不能包含逗号或换行。", status_code=422)
    kind = str(data.get("kind") or "custom").strip().lower()
    if kind not in NODE_GROUP_KINDS:
        raise ToolboxError("INVALID_NODE_GROUP_KIND", "不支持的节点分组类型。", status_code=422)
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    if kind == "custom":
        node_ids = [str(item) for item in (config.get("nodeIds") or []) if item]
        config = {"nodeIds": list(dict.fromkeys(node_ids))}
    elif kind == "region":
        countries = [str(item).strip() for item in (config.get("countries") or []) if item]
        if not countries:
            raise ToolboxError("NODE_GROUP_COUNTRY_REQUIRED", "地域分组至少选择一个国家或地区。", status_code=422)
        node_ids = [str(item) for item in (config.get("nodeIds") or []) if item]
        config = {"countries": list(dict.fromkeys(countries)),
                  "nodeIds": list(dict.fromkeys(node_ids))}
    elif kind == "latency":
        mode = str(config.get("mode") or "top").strip().lower()
        if mode not in {"top", "threshold"}:
            raise ToolboxError("INVALID_NODE_GROUP_LATENCY", "延迟分组模式无效。", status_code=422)
        node_ids = [str(item) for item in (config.get("nodeIds") or []) if item]
        config = {"mode": mode, "count": max(1, int(config.get("count") or 5)),
                  "thresholdMs": max(0, int(config.get("thresholdMs") or 0)),
                  "nodeIds": list(dict.fromkeys(node_ids))}
    return name, kind, config


def save_node_group(data: dict[str, Any], user: User, group_id: str | None = None) -> dict[str, Any]:
    init_database(user.id)
    name, kind, config = _normalise_node_group(data, user)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        now = _now()
        if group_id:
            row = conn.execute("SELECT * FROM csm_node_groups WHERE id=?", (group_id,)).fetchone()
            if not row:
                _not_found("节点分组")
            conn.execute("UPDATE csm_node_groups SET name=?,kind=?,config_json=?,updated_at=? WHERE id=?",
                         (name, kind, _json(config), now, group_id))
        else:
            group_id = _id()
            conn.execute("""INSERT INTO csm_node_groups(id,name,kind,config_json,created_at,updated_at)
              VALUES(?,?,?,?,?,?)""", (group_id, name, kind, _json(config), now, now))
        row = conn.execute("SELECT * FROM csm_node_groups WHERE id=?", (group_id,)).fetchone()
        return _node_group_row(row, _resolve_group_members(conn, row))


def delete_node_group(group_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_node_groups WHERE id=?", (group_id,)).rowcount == 0:
            _not_found("节点分组")


def _expand_node_group_members(conn: sqlite3.Connection, rule_set: dict[str, Any]) -> dict[str, Any]:
    """Resolve node-group references in strategy groups to concrete node refs."""
    groups = rule_set.get("groups")
    if not isinstance(groups, list) or not groups:
        return rule_set
    group_ids: list[str] = []
    for group in groups:
        raw = group.get("nodeGroups") if isinstance(group, dict) else None
        if isinstance(raw, list):
            group_ids.extend(str(item) for item in raw if item)
    group_ids = list(dict.fromkeys(group_ids))
    members_by_id: dict[str, list[str]] = {}
    if group_ids:
        placeholders = ",".join("?" for _ in group_ids)
        rows = {row["id"]: row for row in conn.execute(
            f"SELECT * FROM csm_node_groups WHERE id IN ({placeholders})", group_ids)}
        for group_id in group_ids:
            row = rows.get(group_id)
            members_by_id[group_id] = _resolve_group_members(conn, row) if row else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        proxies = list(group.get("proxies")) if isinstance(group.get("proxies"), list) else []
        raw_ids = group.get("nodeGroups") if isinstance(group.get("nodeGroups"), list) else []
        for group_id in raw_ids:
            proxies.extend(members_by_id.get(str(group_id), []))
        group["proxies"] = list(dict.fromkeys(proxies))
    return rule_set


def _compatible(protocol: str) -> bool:
    return protocol.lower() in CLASH_META_PROXY_TYPES


_PROFILE_WITH_RULE_SET = """SELECT p.*,rs.name AS rule_set_name,rs.updated_at AS rule_set_updated_at
FROM csm_profiles p LEFT JOIN csm_rule_sets rs ON rs.id=p.rule_set_id"""


def _profile_row(row: sqlite3.Row, public_token: str | None = None) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"], "name": row["name"], "settings": _loads(row["settings_json"], {}),
        "ruleSetId": row["rule_set_id"],
        "ruleSetName": row["rule_set_name"] if "rule_set_name" in keys else None,
        "ruleSetUpdatedAt": row["rule_set_updated_at"] if "rule_set_updated_at" in keys else None,
        "publishedAt": row["published_at"], "publishedStatus": row["published_status"],
        "validation": _loads(row["last_validation_json"], []), "subscriptionToken": public_token,
        "createdAt": row["created_at"],
    }


def list_profiles(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute(f"{_PROFILE_WITH_RULE_SET} ORDER BY p.created_at DESC")
        return [_profile_row(row, _decrypt(row["token_encrypted"])) for row in rows]


def _create_token_index(user_id: str, profile_id: str, token: str) -> None:
    with connection_context() as conn:
        now = _now(); conn.execute("INSERT INTO csm_public_tokens VALUES(?,?,?,?,?,?)", (_token_hash(token), user_id, profile_id, 1, now, now))


def _profile_settings(value: Any) -> dict[str, Any]:
    settings = dict(value) if isinstance(value, dict) else {}
    settings["mode"] = "rule"
    output_mode = str(settings.get("ruleProviderOutputMode") or RULE_PROVIDER_OUTPUT_MODE_DEFAULT).lower()
    settings["ruleProviderOutputMode"] = output_mode if output_mode in RULE_PROVIDER_OUTPUT_MODES else RULE_PROVIDER_OUTPUT_MODE_DEFAULT
    return settings


def create_profile(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id); profile_id, token, now = _id(), secrets.token_urlsafe(32), _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("INSERT INTO csm_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)", (profile_id, str(data.get("name") or "未命名聚合")[:120], "clash-meta", _json(_profile_settings(data.get("settings"))), data.get("ruleSetId"), _encrypt(token), None, "draft", "[]", now, now))
        row = conn.execute(f"{_PROFILE_WITH_RULE_SET} WHERE p.id=?", (profile_id,)).fetchone()
    _create_token_index(user.id, profile_id, token)
    return _profile_row(row, token)


def update_profile(profile_id: str, data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    allowed = {"name": "name", "ruleSetId": "rule_set_id"}
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row: _not_found("聚合配置")
        changed: dict[str, Any] = {}
        for inbound, column in allowed.items():
            if inbound in data: changed[column] = data[inbound]
        if "settings" in data: changed["settings_json"] = _json(_profile_settings(data["settings"]))
        if changed:
            sql = ",".join(f"{key}=?" for key in changed)
            conn.execute(f"UPDATE csm_profiles SET {sql},updated_at=? WHERE id=?", (*changed.values(), _now(), profile_id))
        return _profile_row(conn.execute(f"{_PROFILE_WITH_RULE_SET} WHERE p.id=?", (profile_id,)).fetchone())


def delete_profile(profile_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_profiles WHERE id=?", (profile_id,)).rowcount == 0: _not_found("聚合配置")
    with connection_context() as conn: conn.execute("DELETE FROM csm_public_tokens WHERE user_id=? AND profile_id=?", (user.id, profile_id))


def _normalise_legacy_manual_provider(config: dict[str, Any]) -> dict[str, Any]:
    """Keep legacy inline records editable without ever emitting the non-portable type."""
    value = dict(config)
    if value.get("type") == "inline":
        value["type"] = "manual"
    return value


def _rule_provider_row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "providerKey": row["provider_key"],
            "description": row["description"] or "", "config": _normalise_legacy_manual_provider(_loads(row["config_json"], {})),
            "builtin": bool(row["is_builtin"]),
            "updatedAt": row["updated_at"]}


def list_rule_providers(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_rule_provider_row(row) for row in conn.execute(
            "SELECT * FROM csm_rule_providers ORDER BY is_builtin DESC,name COLLATE NOCASE")]


def _download_rule_provider_payload(url: str) -> list[str]:
    content = _download(_require_http_url(url)).decode("utf-8-sig", "replace")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ToolboxError("INVALID_PROVIDER_YAML", "规则订阅内容不是有效的 YAML。", status_code=422) from exc
    payload = document.get("payload") if isinstance(document, dict) else document
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise ToolboxError("INVALID_PROVIDER_PAYLOAD", "规则订阅必须包含字符串数组 payload。", status_code=422)
    result = [item.strip() for item in payload if item.strip()]
    if not result:
        raise ToolboxError("INVALID_PROVIDER_PAYLOAD", "规则订阅中没有可用规则。", status_code=422)
    return result


def _normalise_rule_provider(data: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    name = str(data.get("name") or "").strip()[:120]
    provider_key = str(data.get("providerKey") or "").strip()[:120]
    description = str(data.get("description") or "").strip()[:500]
    config = data.get("config") or {}
    if not name:
        raise ToolboxError("RULE_PROVIDER_NAME_REQUIRED", "请输入 Rule Provider 名称。", status_code=422)
    if not provider_key:
        provider_key = f"rp-{uuid.uuid4().hex}"
    if any(char in provider_key for char in ",\r\n"):
        raise ToolboxError("INVALID_PROVIDER_KEY", "Provider Key 不能包含逗号或换行。", status_code=422)
    if not isinstance(config, dict):
        raise ToolboxError("INVALID_PROVIDER_CONFIG", "Rule Provider 配置必须是对象。", status_code=422)
    provider_type = str(config.get("type") or "").strip().lower()
    if provider_type == "inline":
        provider_type = "manual"  # one-way migration for records created by older versions
    behavior = str(config.get("behavior") or "").strip().lower()
    if behavior not in {"domain", "ipcidr", "classical"}:
        raise ToolboxError("INVALID_PROVIDER_BEHAVIOR", "Behavior 必须是 domain、ipcidr 或 classical。", status_code=422)
    if str(config.get("format") or "").lower() == "mrs":
        raise ToolboxError("INCOMPATIBLE_PROVIDER_FORMAT", "MRS 格式不在当前 Clash Meta YAML 输出范围内，请改用 YAML Provider。", status_code=422)
    if provider_type == "cached":
        source_url = str(config.get("url") or config.get("sourceUrl") or "").strip()
        payload = _download_rule_provider_payload(source_url)
        config = {"type": "cached", "behavior": behavior, "sourceUrl": source_url,
                  "payload": payload, "fetchedAt": _now()}
    elif provider_type == "manual":
        payload = config.get("payload")
        if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
            raise ToolboxError("INVALID_PROVIDER_PAYLOAD", "手写 Rule Provider 的 payload 必须是字符串数组。", status_code=422)
        clean_payload = [item.strip() for item in payload if item.strip()]
        if not clean_payload:
            raise ToolboxError("INVALID_PROVIDER_PAYLOAD", "手写 Rule Provider 至少需要一条规则。", status_code=422)
        config = {"type": "manual", "behavior": behavior, "payload": clean_payload}
    elif provider_type in {"http", "file"}:
        config = {key: value for key, value in config.items() if key in RULE_PROVIDER_FIELDS}
        config["type"] = provider_type
        config["behavior"] = behavior
        if not (config.get("url") or config.get("path")):
            raise ToolboxError("INVALID_PROVIDER_CONFIG", "订阅型 Rule Provider 至少需要 url 或 path。", status_code=422)
    else:
        raise ToolboxError("INVALID_PROVIDER_TYPE", "Rule Provider 仅支持规则订阅、http、file 或自定义规则。", status_code=422)
    return name, provider_key, description, config


def save_rule_provider(data: dict[str, Any], user: User, provider_id: str | None = None) -> dict[str, Any]:
    init_database(user.id)
    name, provider_key, description, config = _normalise_rule_provider(data)
    now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conflict = conn.execute("SELECT id FROM csm_rule_providers WHERE provider_key=? AND id<>?", (provider_key, provider_id or "")).fetchone()
        if conflict:
            raise ToolboxError("PROVIDER_KEY_CONFLICT", "Provider Key 已被其他项目使用。", status_code=409)
        if provider_id:
            row = conn.execute("SELECT * FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone()
            if not row: _not_found("Rule Provider")
            conn.execute("""UPDATE csm_rule_providers SET name=?,provider_key=?,description=?,config_json=?,updated_at=? WHERE id=?""",
                         (name, provider_key, description, _json(config), now, provider_id))
        else:
            provider_id = _id()
            conn.execute("""INSERT INTO csm_rule_providers(
              id,name,provider_key,description,config_json,is_builtin,created_at,updated_at)
              VALUES(?,?,?,?,?,0,?,?)""", (provider_id, name, provider_key, description, _json(config), now, now))
        return _rule_provider_row(conn.execute("SELECT * FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone())


def copy_rule_provider(provider_id: str, mode: str, user: User) -> dict[str, Any]:
    """Copy a Provider while always assigning a new internal Provider Key."""
    init_database(user.id)
    clean_mode = str(mode or "original").strip().lower()
    if clean_mode not in {"original", "manual"}:
        raise ToolboxError("INVALID_PROVIDER_COPY_MODE", "复制方式仅支持原样复制或转为自定义规则。", status_code=422)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone()
        if not row:
            _not_found("Rule Provider")
        stored = _normalise_legacy_manual_provider(_loads(row["config_json"], {}))
        base = str(row["name"] or "Rule Provider")
        names = {str(item["name"]) for item in conn.execute("SELECT name FROM csm_rule_providers")}
    copy_name = f"{base}（副本）"
    sequence = 2
    while copy_name in names:
        copy_name = f"{base}（副本 {sequence}）"
        sequence += 1

    config = dict(stored)
    if clean_mode == "manual":
        provider_type = str(stored.get("type") or "").lower()
        behavior = str(stored.get("behavior") or "domain").lower()
        if provider_type == "manual":
            payload = stored.get("payload") or []
        elif provider_type == "cached":
            source_url = str(stored.get("sourceUrl") or stored.get("url") or "").strip()
            payload = _download_rule_provider_payload(source_url) if source_url else stored.get("payload") or []
        elif provider_type == "http":
            payload = _download_rule_provider_payload(str(stored.get("url") or "").strip())
        else:
            raise ToolboxError(
                "PROVIDER_COPY_TO_MANUAL_UNSUPPORTED",
                "该 Rule Provider 没有可下载的规则订阅链接，无法转为自定义规则。",
                status_code=422,
            )
        config = {"type": "manual", "behavior": behavior, "payload": payload}

    return save_rule_provider({
        "name": copy_name,
        "providerKey": "",
        "description": str(row["description"] or ""),
        "config": config,
    }, user)


def delete_rule_provider(provider_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT is_builtin FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone()
        if not row: _not_found("Rule Provider")
        if bool(row["is_builtin"]):
            raise ToolboxError("BUILTIN_RULE_PROVIDER", "内置 Rule Provider 不能删除，可以编辑其配置。", status_code=409)
        for rule_row in conn.execute("SELECT name,import_meta_json FROM csm_rule_sets"):
            meta = _loads(rule_row["import_meta_json"], {})
            bindings = meta.get("providerBindings") if isinstance(meta, dict) else None
            if isinstance(bindings, dict) and any(
                provider_id in ids for ids in bindings.values() if isinstance(ids, list)
            ):
                raise ToolboxError("RULE_PROVIDER_IN_USE", f"Rule Provider 正被规则库“{rule_row['name']}”使用，请先在策略组中取消选择。", status_code=409)
        conn.execute("DELETE FROM csm_rule_providers WHERE id=?", (provider_id,))


def _remote_provider_output(config: dict[str, Any]) -> dict[str, Any]:
    value = _normalise_legacy_manual_provider(config)
    if value.get("type") not in {"http", "file"} or str(value.get("format") or "").lower() == "mrs":
        return {}
    locations = f"{value.get('url', '')} {value.get('path', '')}".lower()
    if ".mrs" in locations:
        return {}
    return {key: item for key, item in value.items() if key in RULE_PROVIDER_FIELDS and item not in (None, "")}


def _inline_remote_provider_rules(config: dict[str, Any], target: str) -> list[str]:
    """Fetch an HTTP Provider on the server and expand it into concrete rules."""
    value = _normalise_legacy_manual_provider(config)
    url = str(value.get("url") or "").strip()
    if value.get("type") != "http" or not url:
        raise ToolboxError(
            "RULE_PROVIDER_INLINE_REQUIRES_URL",
            "拉取后优先仅支持带 URL 的规则订阅；请改为 URL 优先，或为该 Rule Provider 配置 URL。",
            status_code=422,
        )
    payload = _download_rule_provider_payload(url)
    return _manual_provider_rules({
        "type": "manual",
        "behavior": str(value.get("behavior") or "domain").lower(),
        "payload": payload,
    }, target)


def _manual_provider_rules(config: dict[str, Any], target: str) -> list[str]:
    value = _normalise_legacy_manual_provider(config)
    if value.get("type") not in {"manual", "cached"}:
        return []
    behavior = str(value.get("behavior") or "domain")
    result: list[str] = []
    for raw in value.get("payload") or []:
        item = str(raw).strip()
        if not item:
            continue
        if behavior == "domain":
            suffix = item.startswith("+.") or item.startswith(".")
            domain = item[2:] if item.startswith("+.") else item[1:] if item.startswith(".") else item
            result.append(f"{'DOMAIN-SUFFIX' if suffix else 'DOMAIN'},{domain},{target}")
        elif behavior == "ipcidr":
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ToolboxError("INVALID_PROVIDER_PAYLOAD", f"无效 IP 网段：{item}。", status_code=422) from exc
            rule_type = "IP-CIDR6" if network.version == 6 else "IP-CIDR"
            result.append(f"{rule_type},{item},{target},no-resolve")
        else:
            parts = [part.strip() for part in item.split(",")]
            if len(parts) < 2:
                raise ToolboxError("INVALID_PROVIDER_PAYLOAD", f"classical 规则不完整：{item}。", status_code=422)
            if parts[-1].lower() == "no-resolve":
                result.append(",".join(parts[:-1] + [target, "no-resolve"]))
            else:
                result.append(",".join(parts + [target]))
    return result


def package_rule_providers(provider_ids: list[str], target: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    clean_target = str(target or "").strip()[:120]
    if not clean_target or any(char in clean_target for char in ",\r\n"):
        raise ToolboxError("INVALID_PROVIDER_TARGET", "请输入有效的策略组或节点名称。", status_code=422)
    ordered_ids = list(dict.fromkeys(str(item) for item in provider_ids if item))[:100]
    if not ordered_ids:
        raise ToolboxError("RULE_PROVIDER_REQUIRED", "请至少选择一个 Rule Provider。", status_code=422)
    placeholders = ",".join("?" for _ in ordered_ids)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = {row["id"]: row for row in conn.execute(f"SELECT * FROM csm_rule_providers WHERE id IN ({placeholders})", ordered_ids)}
    if any(item not in rows for item in ordered_ids):
        raise ToolboxError("RULE_PROVIDER_NOT_FOUND", "所选 Rule Provider 已不存在，请刷新后重试。", status_code=404)
    providers: dict[str, Any] = {}
    rules: list[str] = []
    for provider_id in ordered_ids:
        row = rows[provider_id]
        key = str(row["provider_key"])
        if key in providers:
            raise ToolboxError("PROVIDER_KEY_CONFLICT", f"多个项目使用相同 Provider Key：{key}。", status_code=409)
        stored = _loads(row["config_json"], {})
        manual_rules = _manual_provider_rules(stored, clean_target)
        if manual_rules:
            rules.extend(manual_rules)
        else:
            providers[key] = _remote_provider_output(stored)
            rules.append(f"RULE-SET,{key},{clean_target}")
    return {"providers": providers, "rules": rules}


def _rule_row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"],
            "rules": _loads(row["rules_json"], []), "providers": _loads(row["providers_json"], {}),
            "groups": _loads(row["groups_json"], []), "importMeta": _loads(row["import_meta_json"], {}),
            "updatedAt": row["updated_at"]}


def list_rule_sets(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_rule_row(row) for row in conn.execute("SELECT * FROM csm_rule_sets ORDER BY name COLLATE NOCASE")]


def _materialize_rule_provider_bindings(
    conn: sqlite3.Connection, rule_set: dict[str, Any], *, inline_rule_providers: bool
) -> dict[str, Any]:
    """Resolve saved Provider IDs at build time so rule sets never hold Provider snapshots."""
    meta = rule_set.get("importMeta") or {}
    bindings = meta.get("providerBindings") if isinstance(meta, dict) else None
    if not isinstance(bindings, dict):
        return rule_set
    ordered_ids: list[str] = []
    for group in rule_set.get("groups", []):
        ids = bindings.get(str(group.get("name") or "")) or []
        if isinstance(ids, list):
            ordered_ids.extend(str(provider_id) for provider_id in ids if provider_id)
    ordered_ids = list(dict.fromkeys(ordered_ids))[:500]
    rows: dict[str, sqlite3.Row] = {}
    if ordered_ids:
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = {row["id"]: row for row in conn.execute(
            f"SELECT * FROM csm_rule_providers WHERE id IN ({placeholders})", ordered_ids)}
    providers: dict[str, Any] = {}
    generated_rules: list[str] = []
    missing_ids: list[str] = []
    for group in rule_set.get("groups", []):
        group_name = str(group.get("name") or "")
        ids = bindings.get(group_name) or []
        if not isinstance(ids, list):
            continue
        for provider_id in dict.fromkeys(str(item) for item in ids if item):
            row = rows.get(provider_id)
            if not row:
                missing_ids.append(provider_id)
                continue
            provider_key = str(row["provider_key"])
            stored = _loads(row["config_json"], {})
            manual_rules = (
                _manual_provider_rules(stored, group_name)
                if not inline_rule_providers
                else _manual_provider_rules(stored, group_name)
                or _inline_remote_provider_rules(stored, group_name)
            )
            if manual_rules:
                generated_rules.extend(manual_rules)
            else:
                providers[provider_key] = _remote_provider_output(stored)
                generated_rules.append(f"RULE-SET,{provider_key},{group_name}")
    base_rules = [
        rule for rule in rule_set.get("rules", [])
        if not (isinstance(rule, str) and rule.split(",", 1)[0].strip().upper() == "RULE-SET")
    ]
    material = dict(rule_set)
    material["providers"] = providers
    material["rules"] = generated_rules + base_rules
    material_meta = dict(meta)
    material_meta["missingProviderIds"] = list(dict.fromkeys(missing_ids))
    material["importMeta"] = material_meta
    return material


def _normalise_rules(data: dict[str, Any]) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]]]:
    rules = data.get("rules") or []
    providers = data.get("providers") or data.get("ruleProviders") or {}
    groups = data.get("groups") or data.get("proxyGroups") or []
    if not isinstance(rules, list) or not isinstance(providers, dict) or not isinstance(groups, list):
        raise ToolboxError("INVALID_RULE_SET", "规则、规则提供器和策略组必须分别为数组、对象和数组。", status_code=422)
    clean_groups = [dict(item) for item in groups if isinstance(item, dict) and item.get("name")]
    names = [str(group.get("name") or "").strip() for group in clean_groups]
    if any(not name or any(char in name for char in ",\r\n") for name in names):
        raise ToolboxError("INVALID_STRATEGY_GROUP_NAME", "策略组名称不能为空，且不能包含逗号或换行。", status_code=422)
    if len(names) != len(set(names)):
        raise ToolboxError("DUPLICATE_STRATEGY_GROUP", "策略组名称不能重复。", status_code=422)
    return rules[:5000], providers, clean_groups[:1000]


def save_rule_set(data: dict[str, Any], user: User, rule_set_id: str | None = None) -> dict[str, Any]:
    init_database(user.id); rules, providers, groups = _normalise_rules(data); now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if rule_set_id:
            row = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (rule_set_id,)).fetchone()
            if not row: _not_found("规则库")
            group_name = "默认分组"
            import_meta = data.get("importMeta") if isinstance(data.get("importMeta"), dict) else _loads(row["import_meta_json"], {})
            conn.execute("UPDATE csm_rule_sets SET name=?,group_name=?,rules_json=?,providers_json=?,groups_json=?,import_meta_json=?,updated_at=? WHERE id=?", (str(data.get("name") or row["name"])[:120], group_name, _json(rules), _json(providers), _json(groups), _json(import_meta), now, rule_set_id))
        else:
            rule_set_id = _id()
            group_name = "默认分组"
            conn.execute("""INSERT INTO csm_rule_sets(
              id,name,rules_json,providers_json,groups_json,import_meta_json,created_at,updated_at,group_name)
              VALUES(?,?,?,?,?,?,?,?,?)""", (rule_set_id, str(data.get("name") or "规则库")[:120], _json(rules), _json(providers), _json(groups), _json(data.get("importMeta") or {}), now, now, group_name))
        return _rule_row(conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (rule_set_id,)).fetchone())


def import_rule_set(data: dict[str, Any], user: User) -> dict[str, Any]:
    """Import rules and groups without importing proxy nodes or storing Provider snapshots."""
    content = str(data.get("content") or "")
    if not content and data.get("url"):
        content = _download(_require_http_url(str(data["url"]))).decode("utf-8-sig", "replace")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ToolboxError("INVALID_RULE_YAML", "规则 YAML 无法解析。", status_code=422) from exc
    if not isinstance(document, dict):
        raise ToolboxError("INVALID_RULE_YAML", "规则内容必须是 YAML 对象。", status_code=422)

    imported_providers = document.get("rule-providers") or {}
    if not isinstance(imported_providers, dict):
        raise ToolboxError("INVALID_RULE_YAML", "rule-providers 必须是对象。", status_code=422)
    library_by_key = {item["providerKey"]: item for item in list_rule_providers(user)}
    for key, config in imported_providers.items():
        provider_key = str(key).strip()
        if not provider_key or not isinstance(config, dict) or provider_key in library_by_key:
            continue
        created = save_rule_provider({
            "name": provider_key,
            "providerKey": provider_key,
            "description": "从规则订阅导入",
            "config": config,
        }, user)
        library_by_key[provider_key] = created

    bindings: dict[str, list[str]] = {}
    rules = document.get("rules") or []
    if not isinstance(rules, list):
        raise ToolboxError("INVALID_RULE_YAML", "rules 必须是数组。", status_code=422)
    for rule in rules:
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 3 or parts[0].upper() != "RULE-SET":
            continue
        provider = library_by_key.get(parts[1])
        if provider:
            bindings.setdefault(parts[2], []).append(provider["id"])
    bindings = {name: list(dict.fromkeys(ids)) for name, ids in bindings.items()}

    return save_rule_set({
        "name": data.get("name") or "导入规则",
        "rules": rules,
        "providers": {},
        "groups": document.get("proxy-groups", []),
        "importMeta": {
            "importedAt": _now(),
            "source": "url" if data.get("url") else "paste",
            "providerBindings": bindings,
            "strategyGroupEditor": True,
        },
    }, user)


def delete_rule_set(rule_set_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if conn.execute("DELETE FROM csm_rule_sets WHERE id=?", (rule_set_id,)).rowcount == 0: _not_found("规则库")


def _rule_referenced_nodes(conn: sqlite3.Connection, rule_set: dict[str, Any] | None) -> list[sqlite3.Row]:
    """Return nodes explicitly used by strategy groups or direct rules."""
    if rule_set is None: return []
    references: list[str] = []
    for group in rule_set.get("groups") or []:
        if not isinstance(group, dict): continue
        references.extend(str(item) for item in (group.get("proxies") or []) if item)
    for rule in rule_set.get("rules") or []:
        target = _rule_target(rule)
        if target: references.append(target)
    all_nodes = list(conn.execute("SELECT * FROM csm_nodes").fetchall())
    nodes_by_id: dict[str, sqlite3.Row] = {}
    for reference in dict.fromkeys(references):
        for row in all_nodes:
            if reference in {str(row["name"] or ""), str(row["alias"] or "")}:
                nodes_by_id[str(row["id"])] = row
    return list(nodes_by_id.values())


def _profile_material(conn: sqlite3.Connection, profile_id: str) -> tuple[sqlite3.Row, list[sqlite3.Row], dict[str, Any] | None]:
    profile = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile: _not_found("聚合配置")
    rule = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (profile["rule_set_id"],)).fetchone() if profile["rule_set_id"] else None
    rule_set = (
        _materialize_rule_provider_bindings(
            conn,
            _rule_row(rule),
            inline_rule_providers=_profile_rule_provider_output_mode(profile) == "inline",
        )
        if rule
        else None
    )
    if rule_set is not None:
        rule_set = _expand_node_group_members(conn, rule_set)
    return profile, _rule_referenced_nodes(conn, rule_set), rule_set


def _profile_rule_provider_output_mode(profile: sqlite3.Row) -> str:
    settings = _loads(profile["settings_json"], {})
    value = str(settings.get("ruleProviderOutputMode") or RULE_PROVIDER_OUTPUT_MODE_DEFAULT).lower()
    return value if value in RULE_PROVIDER_OUTPUT_MODES else RULE_PROVIDER_OUTPUT_MODE_DEFAULT


def _rule_string_parts(rule: str) -> tuple[str, str, str]:
    """Return material before the policy, the policy, and trailing options."""
    head, separator, tail = rule.rpartition(",")
    if not separator:
        return "", "", ""
    if tail.strip().lower() == "no-resolve":
        prefix, policy_separator, policy = head.rpartition(",")
        if policy_separator:
            return prefix, policy.strip(), f",{tail}"
    return head, tail.strip(), ""


def _rule_target(rule: Any) -> str:
    if isinstance(rule, str): return _rule_string_parts(rule)[1]
    if isinstance(rule, dict): return str(rule.get("target") or rule.get("policy") or rule.get("proxy") or "")
    return ""


def _validate_material(profile: sqlite3.Row, nodes: list[sqlite3.Row], rule_set: dict[str, Any] | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if rule_set is None:
        messages.append({"level": "error", "code": "PROFILE_RULE_SET_REQUIRED", "message": "聚合配置必须关联规则组；输出节点由规则组决定。"})
    for item in nodes:
        label = item["alias"] or item["name"]
        if not _compatible(str(item["protocol"])):
            messages.append({"level": "error", "code": "INCOMPATIBLE_OUTPUT", "message": f"节点 {label}（{item['protocol']}）不受 Clash Meta 支持。"})
    if not rule_set: return messages
    groups = rule_set["groups"]; names = {str(group.get("name")) for group in groups if group.get("name")}
    _, node_references, ambiguous_references = _node_output_material(nodes)
    node_names = set(node_references) | set(ambiguous_references)
    graph: dict[str, set[str]] = {}
    for group in groups:
        name = str(group.get("name") or "")
        if not name: continue
        group_type = str(group.get("type") or "select")
        if group_type not in COMPATIBLE_GROUP_TYPES:
            messages.append({"level": "error", "code": "INCOMPATIBLE_GROUP", "message": f"策略组 {name} 使用了非兼容类型：{group_type}。"})
        refs = group.get("proxies") or []
        if not isinstance(refs, list):
            messages.append({"level": "error", "code": "INVALID_GROUP", "message": f"策略组 {name} 的成员必须是列表。"}); continue
        graph[name] = {str(ref) for ref in refs if str(ref) in names}
        for ref in refs:
            if str(ref) in ambiguous_references and str(ref) not in names:
                messages.append({"level": "error", "code": "AMBIGUOUS_NODE_REFERENCE", "message": f"策略组 {name} 引用了重名节点：{ref}，请改用唯一别名。"})
            elif str(ref) not in names and str(ref) not in node_names and str(ref) not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
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
        if not isinstance(rule, str):
            messages.append({"level": "error", "code": "INCOMPATIBLE_RULE", "message": "仅支持 Clash Meta 的字符串规则。"})
            continue
        rule_type = rule.split(",", 1)[0].strip().upper()
        if rule_type not in CLASH_META_RULE_TYPES:
            messages.append({"level": "error", "code": "INCOMPATIBLE_RULE", "message": f"规则类型 {rule_type or '空'} 不属于兼容范围。"})
        target = _rule_target(rule)
        if target in ambiguous_references and target not in names:
            messages.append({"level": "error", "code": "AMBIGUOUS_NODE_REFERENCE", "message": f"规则引用了重名节点：{target}，请改用唯一别名。"})
        elif target and target not in names and target not in node_names and target not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
            messages.append({"level": "error", "code": "MISSING_RULE_TARGET", "message": f"规则引用不存在的策略组或节点：{target}。"})
    for index, rule in enumerate(rule_set["rules"]):
        value = rule if isinstance(rule, str) else str(rule.get("type") or "")
        if str(value).upper().startswith("MATCH") and index != len(rule_set["rules"]) - 1:
            messages.append({"level": "error", "code": "MATCH_POSITION", "message": "MATCH 终止规则必须位于规则列表末尾。"})
    for provider_id in (rule_set.get("importMeta") or {}).get("missingProviderIds", []):
        messages.append({"level": "error", "code": "MISSING_RULE_PROVIDER", "message": f"规则库引用的 Rule Provider 已不存在：{str(provider_id)[:12]}…。"})
    for name, provider in rule_set["providers"].items():
        valid_remote = isinstance(provider, dict) and provider.get("type") in {"http", "file"} and provider.get("behavior") in {"domain", "ipcidr", "classical"} and bool(provider.get("url") or provider.get("path"))
        if not valid_remote:
            messages.append({"level": "error", "code": "INCOMPATIBLE_PROVIDER", "message": f"Rule Provider {name} 不是兼容的 http/file Provider。"})
    return messages


def validate_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile, nodes, rule_set = _profile_material(conn, profile_id)
        messages = _validate_material(profile, nodes, rule_set)
        conn.execute("UPDATE csm_profiles SET last_validation_json=?,updated_at=? WHERE id=?", (_json(messages), _now(), profile_id))
    return {"profileId": profile_id, "valid": not any(m["level"] == "error" for m in messages), "messages": messages, "outputNodeCount": len(nodes)}


def _proxy_output(config: dict[str, Any], protocol: str) -> dict[str, Any]:
    allowed = PROXY_COMMON_FIELDS | PROXY_FIELDS.get(protocol, set())
    return {key: value for key, value in config.items() if key in allowed and value not in (None, "", [], {})}


def _group_output(group: dict[str, Any]) -> dict[str, Any] | None:
    group_type = str(group.get("type") or "select")
    if group_type not in COMPATIBLE_GROUP_TYPES:
        return None
    value = {key: item for key, item in group.items() if key in GROUP_FIELDS and item not in (None, "")}
    value["type"] = group_type
    return value


def _dns_output(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if key in DNS_FIELDS}


def _node_output_material(nodes: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], dict[str, str], set[str]]:
    """Build unique proxy names and resolve source names/aliases to them."""
    used: dict[str, int] = {}; output = []; candidates: dict[str, set[str]] = {}
    for row in nodes:
        protocol = str(row["protocol"] or "").lower()
        if not _compatible(protocol):
            continue
        value = _proxy_output(_loads(row["config_json"], {}), protocol)
        original = str(row["name"] or value.get("name") or "")
        alias = str(row["alias"] or "")
        base = alias or original
        count = used.get(base, 0); used[base] = count + 1
        output_name = f"{base} ({count + 1})" if count else base
        value["name"] = output_name
        output.append(value)
        for reference in {original, alias} - {""}:
            candidates.setdefault(reference, set()).add(output_name)
    ambiguous = {reference for reference, values in candidates.items() if len(values) > 1}
    references = {reference: next(iter(values)) for reference, values in candidates.items() if len(values) == 1}
    return output, references, ambiguous


def _rewrite_rule_target(rule: Any, references: dict[str, str], group_names: set[str]) -> Any:
    if isinstance(rule, str):
        head, target, suffix = _rule_string_parts(rule)
        if target and target not in group_names and target in references:
            return f"{head},{references[target]}{suffix}"
        return rule
    if isinstance(rule, dict):
        value = dict(rule)
        for key in ("target", "policy", "proxy"):
            target = str(value.get(key) or "")
            if target and target not in group_names and target in references:
                value[key] = references[target]
                break
        return value
    return rule


def _rewrite_rule_material(rule_set: dict[str, Any], references: dict[str, str]) -> tuple[list[dict[str, Any]], list[Any]]:
    groups = [value for group in rule_set["groups"] if (value := _group_output(dict(group))) is not None]
    group_names = {str(group.get("name")) for group in groups if group.get("name")}
    for group in groups:
        proxies = group.get("proxies")
        if isinstance(proxies, list):
            group["proxies"] = [references.get(str(ref), str(ref)) if str(ref) not in group_names else ref for ref in proxies]
    rules = [_rewrite_rule_target(rule, references, group_names) for rule in rule_set["rules"]]
    return groups, rules


def _build_config(profile: sqlite3.Row, nodes: list[sqlite3.Row], rule_set: dict[str, Any] | None) -> dict[str, Any]:
    settings = _loads(profile["settings_json"], {})
    proxies, references, _ = _node_output_material(nodes)
    config: dict[str, Any] = {"mixed-port": int(settings.get("mixedPort", 7890)), "allow-lan": bool(settings.get("allowLan", False)), "mode": "rule", "ipv6": bool(settings.get("ipv6", False)), "proxies": proxies}
    dns = _dns_output(settings.get("dns"))
    if dns: config["dns"] = dns
    if rule_set:
        groups, rules = _rewrite_rule_material(rule_set, references)
        config["proxy-groups"] = groups
        config["rules"] = [rule for rule in rules if isinstance(rule, str)]
        providers = {key: output for key, provider in rule_set["providers"].items()
                     if (output := _remote_provider_output(provider))}
        if providers: config["rule-providers"] = providers
    return config


def preview_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile, nodes, rule_set = _profile_material(conn, profile_id)
        messages = _validate_material(profile, nodes, rule_set)
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
    conn.execute("DELETE FROM csm_published_snapshots WHERE profile_id=?", (profile_id,))
    conn.execute("INSERT INTO csm_published_snapshots VALUES(?,?,?,?,?)", (_id(), profile_id, _encrypt(content), hashlib.sha256(content.encode()).hexdigest(), now))


def public_subscription(
    token: str, *, client_ip: str = "", user_agent: str = ""
) -> tuple[str, str, str, str] | None:
    """Return the current publication and record an aggregate-link request."""
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
            content = _decrypt(snapshot["content_encrypted"])
            try:
                conn.execute(
                    "INSERT INTO csm_subscription_requests VALUES(?,?,?,?,?,?)",
                    (_id(), index["profile_id"], _now(), client_ip[:64], user_agent[:300], 200),
                )
            except sqlite3.Error:
                pass
            return content, snapshot["content_hash"], snapshot["created_at"], profile["name"]
    except (sqlite3.Error, ToolboxError): return None


def _latest_node_probes(conn: sqlite3.Connection) -> tuple[dict[str, list[sqlite3.Row]], dict[str, list[sqlite3.Row]]]:
    rows = conn.execute("""
      SELECT n.id,n.name,n.alias,n.protocol,p.reachable,p.latency_ms,p.created_at AS probe_at
      FROM csm_nodes n LEFT JOIN csm_probe_results p ON p.id=(
        SELECT p2.id FROM csm_probe_results p2 WHERE p2.node_id=n.id
        ORDER BY p2.created_at DESC,p2.rowid DESC LIMIT 1)
      ORDER BY n.rowid
    """).fetchall()
    aliases: dict[str, list[sqlite3.Row]] = {}
    originals: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        alias = str(row["alias"] or "")
        name = str(row["name"] or "")
        if alias: aliases.setdefault(alias, []).append(row)
        if name: originals.setdefault(name, []).append(row)
    return aliases, originals


def _probe_for_output_name(name: str, aliases: dict[str, list[sqlite3.Row]], originals: dict[str, list[sqlite3.Row]]) -> sqlite3.Row | None:
    if name in aliases: return aliases[name][0]
    if name in originals: return originals[name][0]
    match = re.fullmatch(r"(.+) \((\d+)\)", name)
    if not match: return None
    base, position = match.group(1), int(match.group(2)) - 1
    values = aliases.get(base) or originals.get(base) or []
    return values[position] if 0 <= position < len(values) else None


def _public_group_details(conn: sqlite3.Connection, rule_row: sqlite3.Row | None, groups: list[dict[str, Any]]) -> None:
    """Attach safe Provider payloads to the strategy groups that use them."""
    if rule_row is None: return
    meta = _loads(rule_row["import_meta_json"], {})
    bindings = meta.get("providerBindings") if isinstance(meta, dict) else None
    if not isinstance(bindings, dict): return
    provider_ids = [str(item) for values in bindings.values() if isinstance(values, list) for item in values if item]
    provider_rows: dict[str, sqlite3.Row] = {}
    for provider_id in dict.fromkeys(provider_ids):
        row = conn.execute("SELECT * FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone()
        if row: provider_rows[provider_id] = row
    for group in groups:
        raw_ids = bindings.get(str(group.get("name") or ""))
        if not isinstance(raw_ids, list):
            group["providers"] = []
            continue
        providers: list[dict[str, Any]] = []
        for provider_id in dict.fromkeys(str(item) for item in raw_ids if item):
            row = provider_rows.get(provider_id)
            if not row: continue
            config = _normalise_legacy_manual_provider(_loads(row["config_json"], {}))
            payload = [str(item) for item in config.get("payload") or [] if str(item).strip()]
            providers.append({
                "name": str(row["name"]), "kind": "规则订阅" if config.get("type") in {"cached", "http"} else "自定义",
                "behavior": str(config.get("behavior") or ""), "ruleCount": len(payload), "payload": payload[:200],
            })
        group["providers"] = providers


def _public_subscription_requests(
    conn: sqlite3.Connection, profile_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    rows = conn.execute("""
      SELECT id,requested_at,client_ip,user_agent,status_code
      FROM csm_subscription_requests
      WHERE profile_id=?
      ORDER BY requested_at DESC,rowid DESC LIMIT ?
    """, (profile_id, limit)).fetchall()
    return [{"id": row["id"], "requestedAt": row["requested_at"], "clientIp": row["client_ip"],
             "userAgent": row["user_agent"], "statusCode": row["status_code"]} for row in rows]


def public_subscription_details(token: str) -> dict[str, Any] | None:
    """Return a safe visual summary of the current published subscription."""
    if not token or len(token) > 300: return None
    with connection_context() as conn:
        index = conn.execute("SELECT user_id,profile_id FROM csm_public_tokens WHERE token_hash=? AND enabled=1", (_token_hash(token),)).fetchone()
    if not index: return None
    try:
        init_database(index["user_id"])
        with user_tool_connection_context(index["user_id"], TOOL_ID) as conn:
            profile = conn.execute(f"{_PROFILE_WITH_RULE_SET} WHERE p.id=?", (index["profile_id"],)).fetchone()
            snapshot = conn.execute("SELECT * FROM csm_published_snapshots WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (index["profile_id"],)).fetchone()
            if not profile or not snapshot: return None
            content = _decrypt(snapshot["content_encrypted"])
            rule_row = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (profile["rule_set_id"],)).fetchone() if profile["rule_set_id"] else None
            aliases, originals = _latest_node_probes(conn)
            try: document = yaml.safe_load(content) or {}
            except yaml.YAMLError: return None
            if not isinstance(document, dict): return None
            groups = [dict(item) for item in document.get("proxy-groups") or [] if isinstance(item, dict)]
            try: _public_group_details(conn, rule_row, groups)
            except sqlite3.Error: groups = [group for group in groups if "providers" not in group]
            try: request_runs = _public_subscription_requests(conn, index["profile_id"])
            except sqlite3.Error: request_runs = []
    except (sqlite3.Error, ToolboxError): return None
    proxies: list[dict[str, Any]] = []
    for item in document.get("proxies") or []:
        if not isinstance(item, dict): continue
        value = {"name": str(item.get("name") or ""), "type": str(item.get("type") or ""),
                 "server": str(item.get("server") or ""), "port": item.get("port")}
        probe = _probe_for_output_name(value["name"], aliases, originals)
        value.update({"latencyMs": probe["latency_ms"] if probe else None,
                      "reachable": bool(probe["reachable"]) if probe else None,
                      "checkedAt": probe["probe_at"] if probe else None})
        proxies.append(value)
    return {"name": profile["name"], "publishedAt": snapshot["created_at"], "contentHash": snapshot["content_hash"],
            "mode": str(document.get("mode") or "rule"), "proxies": proxies, "groups": groups,
            "ruleSetName": profile["rule_set_name"], "ruleSetUpdatedAt": profile["rule_set_updated_at"],
            "requestRuns": request_runs, "yaml": content}


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
        profiles = [_profile_row(r) for r in conn.execute(f"{_PROFILE_WITH_RULE_SET} ORDER BY p.updated_at DESC LIMIT 12")]
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


def _rebuild_changed_published_profiles(user: User) -> None:
    """Refresh published YAML only when its content actually changes.

    Latency groups can alter strategy-group members after a probe. Comparing the
    generated hash avoids creating a new snapshot every two minutes when the
    ordered member list is unchanged.
    """
    for profile in list_profiles(user):
        if not profile["publishedAt"]: continue
        try:
            preview = preview_profile(profile["id"], user)
            if not preview["valid"]:
                with user_tool_connection_context(user.id, TOOL_ID) as conn:
                    conn.execute("UPDATE csm_profiles SET published_status='degraded',updated_at=? WHERE id=? AND published_status<>'degraded'", (_now(), profile["id"]))
                continue
            digest = hashlib.sha256(preview["yaml"].encode()).hexdigest()
            with user_tool_connection_context(user.id, TOOL_ID) as conn:
                snapshot = conn.execute("SELECT content_hash FROM csm_published_snapshots WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (profile["id"],)).fetchone()
            if snapshot and snapshot["content_hash"] == digest:
                if profile["publishedStatus"] != "published" or profile["validation"] != preview["messages"]:
                    with user_tool_connection_context(user.id, TOOL_ID) as conn:
                        conn.execute("UPDATE csm_profiles SET published_status='published',last_validation_json=?,updated_at=? WHERE id=?", (_json(preview["messages"]), _now(), profile["id"]))
                continue
            publish_profile(profile["id"], user)
        except ToolboxError:
            with user_tool_connection_context(user.id, TOOL_ID) as conn:
                conn.execute("UPDATE csm_profiles SET published_status='degraded',updated_at=? WHERE id=? AND published_status<>'degraded'", (_now(), profile["id"]))


def probe_all_nodes() -> None:
    """Scheduler entry: probe every node every two minutes for every user."""
    for user_id, _path in list_user_tool_dbs(TOOL_ID):
        init_database(user_id)
        class ScheduledUser:  # noqa: D101
            id = user_id
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            node_ids = [row["id"] for row in conn.execute("SELECT id FROM csm_nodes")]
            has_latency_group = bool(conn.execute("SELECT 1 FROM csm_node_groups WHERE kind='latency' LIMIT 1").fetchone())
        for offset in range(0, len(node_ids), 1000):
            probe_nodes(node_ids[offset:offset + 1000], ScheduledUser())  # type: ignore[arg-type]
        if has_latency_group:
            _rebuild_changed_published_profiles(ScheduledUser())  # type: ignore[arg-type]
