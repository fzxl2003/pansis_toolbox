"""Private storage, parsing and publishing for the AirPlay subscription manager.

The module deliberately keeps network acquisition separate from parsing.  A
failed refresh can therefore never destroy the last successful snapshot or an
already published configuration.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import re
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlencode, unquote, urlsplit

import httpx
import yaml
from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import connection_context, list_user_tool_dbs, user_tool_connection_context
from backend.app.services.auth_service import User, hash_password, verify_password
from backend.app.services.data_management import DataCategory, register_tool_categories

TOOL_ID = "airplay_subscription_manager"
DEFAULT_REFRESH_SECONDS = 6 * 3600
DEFAULT_RULE_PROVIDER_REFRESH_SECONDS = 24 * 3600
DEFAULT_NODE_PROBE_SECONDS = 120
DEFAULT_PROFILE_REFRESH_SECONDS = 6 * 3600
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_SECONDS = 0.35
PARSEABLE_URIS = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic"}
AIRPLAY_STRUCTURED_TYPES = {"http", "socks5", "snell", "wireguard"}
AIRPLAY_PROXY_TYPES = PARSEABLE_URIS | AIRPLAY_STRUCTURED_TYPES
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
GEOIP_API_BASE = "https://api.ip.sb/geoip"
GEOIP_USER_AGENT = "PansisToolbox-AirPlaySubscriptionManager/1.0"
GEOIP_MAX_NODES = 1000
GEOIP_FAILURE_RETRY_SECONDS = 10 * 60
GEOIP_MIN_REQUEST_INTERVAL = 0.2
AIRPLAY_RULE_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR", "GEOIP", "GEOSITE", "DST-PORT", "SRC-PORT", "PROCESS-NAME", "PROCESS-PATH", "RULE-SET", "MATCH"}
BUILTIN_RULE_PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": "builtin-provider-ai", "name": "AI 平台", "providerKey": "ai-platforms", "description": "OpenAI、Claude、Gemini 等常见生成式 AI 平台。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ai-!cn.yaml", "path": "./ruleset/ai-platforms.yaml"}},
    {"id": "builtin-provider-google", "name": "谷歌平台", "providerKey": "google", "description": "Google、YouTube、Gmail 及相关服务。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/google.yaml", "path": "./ruleset/google.yaml"}},
    {"id": "builtin-provider-overseas", "name": "常见国外平台", "providerKey": "common-overseas", "description": "常见非中国大陆互联网平台域名集合。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/geolocation-!cn.yaml", "path": "./ruleset/common-overseas.yaml"}},
    {"id": "builtin-provider-github", "name": "GitHub", "providerKey": "github", "description": "GitHub 及其静态资源、代码托管相关域名。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/github.yaml", "path": "./ruleset/github.yaml"}},
    {"id": "builtin-provider-media", "name": "海外影音娱乐", "providerKey": "overseas-media", "description": "常见海外流媒体、直播与影音平台。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-entertainment.yaml", "path": "./ruleset/overseas-media.yaml"}},
    {"id": "builtin-provider-ads", "name": "广告拦截", "providerKey": "ads", "description": "常见广告、追踪与营销域名集合，可用于 REJECT 策略。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ads-all.yaml", "path": "./ruleset/ads.yaml"}},
    {"id": "builtin-provider-cn", "name": "中国大陆", "providerKey": "china", "description": "中国大陆常见网站与服务域名，可用于 DIRECT 策略。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/cn.yaml", "path": "./ruleset/china.yaml"}},
    {"id": "builtin-provider-private", "name": "私有网络", "providerKey": "private-network", "description": "局域网、私有域名及本地服务，可用于 DIRECT 策略。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/private.yaml", "path": "./ruleset/private-network.yaml"}},
    {"id": "builtin-provider-microsoft", "name": "Microsoft", "providerKey": "microsoft", "description": "Windows、Office、OneDrive、Xbox 等 Microsoft 服务。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/microsoft.yaml", "path": "./ruleset/microsoft.yaml"}},
    {"id": "builtin-provider-apple", "name": "Apple", "providerKey": "apple", "description": "App Store、iCloud、Apple 更新及相关服务。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/apple.yaml", "path": "./ruleset/apple.yaml"}},
    {"id": "builtin-provider-crypto", "name": "加密货币", "providerKey": "cryptocurrency", "description": "Binance、OKX、Coinbase、Bybit 等交易所，以及行情、钱包和区块链服务。", "config": {"type": "http", "behavior": "domain", "interval": 86400, "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-cryptocurrency.yaml", "path": "./ruleset/cryptocurrency.yaml"}},
    {"id": "builtin-provider-cn-stocks", "name": "国内股票平台", "providerKey": "cn-stock-platforms", "description": "同花顺、东方财富、雪球、通达信、大智慧、财联社及和讯，可用于 DIRECT 策略。", "config": {"type": "manual", "behavior": "domain", "payload": [
        "+.10jqka.com.cn", "+.ths123.com", "+.eastmoney.com", "+.dfcfw.com",
        "+.1234567.com.cn", "+.18.cn", "+.18.com.cn", "+.guba.com.cn",
        "+.xueqiu.com", "+.snowballsecurities.com", "+.tdx.com.cn",
        "+.tdx.com", "+.gw.com.cn", "+.dzh.com.cn", "+.cls.cn", "+.hexun.com",
    ]}},
    # Keep the Steam ID/key so existing strategy bindings continue to work.
    # Steam download hosts can live below steampowered.com / steamstatic.com;
    # only include selected service hosts, never those broad parent suffixes.
    # Sources: v2fly/domain-list-community data/{steam,playstation,nintendo,xbox}.
    {"id": "builtin-provider-steam", "name": "游戏平台", "providerKey": "steam", "description": "Steam 商店与社区、PlayStation、Nintendo/Switch、Xbox、Epic、EA、Ubisoft、Battle.net、GOG 和 Riot；排除 Steam 游戏下载 CDN。", "config": {"type": "manual", "behavior": "domain", "payload": [
        "steampowered.com", "www.steampowered.com", "store.steampowered.com",
        "checkout.steampowered.com", "help.steampowered.com", "login.steampowered.com",
        "api.steampowered.com", "partner.steampowered.com", "+.steamcommunity.com",
        "+.steam-chat.com", "+.steam-api.com", "+.steam.tv", "+.s.team",
        "+.steamdeck.com", "+.valvesoftware.com", "+.valve.net",
        "community.steamstatic.com", "store.steamstatic.com", "shared.steamstatic.com",
        "steamcommunity-a.akamaihd.net", "steamstore-a.akamaihd.net",
        "steambroadcast.akamaized.net", "steammobile.akamaized.net",
        "+.playstation", "+.playstation.com", "+.playstation.net", "+.sonyentertainmentnetwork.com",
        "+.nintendo.com", "+.nintendo.net", "+.nintendo.co.jp", "+.nintendo.co.uk",
        "+.nintendo.co.kr", "+.nintendo.com.hk", "+.nintendo.tw", "+.nintendo.eu",
        "+.nintendo.de", "+.nintendo.fr", "+.nintendo.it", "+.nintendo.es",
        "+.nintendo.com.au", "+.nintendonetwork.net", "+.nintendoswitch.com",
        "+.nintendoswitch.net", "+.nintendoswitch.cn", "+.nintendoswitch.com.cn",
        "+.xbox", "+.xbox.com", "+.xboxlive.com", "+.xboxlive.cn",
        "+.xboxservices.com", "+.xboxgamepass.com", "+.gamepass.com",
        "+.epicgames.com", "+.epicgames.dev", "+.unrealengine.com", "+.fortnite.com",
        "+.ea.com", "+.origin.com", "+.ubi.com", "+.ubisoft.com", "+.ubisoftconnect.com",
        "+.battle.net", "+.battlenet.com", "+.blizzard.com", "+.blizzard.net",
        "+.gog.com", "+.gog-statics.com", "+.riotgames.com", "+.riotcdn.net",
        "+.leagueoflegends.com", "+.playvalorant.com", "+.rockstargames.com",
        "+.socialclub.rockstargames.com", "+.bethesda.net",
    ]}},
)
_initialized: set[str] = set()
_init_lock = threading.Lock()
_geoip_request_lock = threading.Lock()
_geoip_last_request_at = 0.0
_geoip_job_users: set[str] = set()
_geoip_job_lock = threading.Lock()

register_tool_categories(TOOL_ID, [
    DataCategory("configuration", ["csm_sources", "csm_nodes", "csm_node_sources", "csm_profiles", "csm_rule_sets", "csm_rule_providers", "csm_node_groups", "csm_published_snapshots", "csm_settings"], None, "订阅源、节点、配置、自动更新设置和最后有效发布版本"),
    DataCategory("history", ["csm_source_snapshots", "csm_refresh_runs", "csm_profile_refresh_runs", "csm_probe_results", "csm_ip_geo_cache", "csm_subscription_requests"], "created_at", "订阅刷新、聚合配置更新、TCP 探测、GeoIP 缓存和聚合订阅请求记录"),
    DataCategory("public_tokens", ["csm_public_tokens"], None, "公开订阅令牌索引", storage="platform_db", user_id_column="user_id"),
])


NODE_GROUP_KINDS = {"custom", "region", "latency"}
# Common Chinese display labels for ISO country codes returned by GeoIP.  The
# matchers themselves are no longer used to decide region-group membership.
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
COUNTRY_LABELS = {code: label for code, label, _patterns in COUNTRY_MATCHERS}

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
        if item["id"] == "builtin-provider-steam":
            legacy = conn.execute("SELECT * FROM csm_rule_providers WHERE id=? AND is_builtin=1", (item["id"],)).fetchone()
            if legacy:
                config = _loads(legacy["config_json"], {})
                # Upgrade only the original built-in, preserving user edits and copies.
                original = {"type": "http", "behavior": "domain",
                            "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/steam.yaml",
                            "path": "./ruleset/steam.yaml"}
                if (legacy["name"] == "Steam" and legacy["provider_key"] == "steam"
                        and legacy["description"] == "Steam 商店、客户端、游戏下载与社区服务。"
                        and all(config.get(key) == value for key, value in original.items())
                        and set(config) <= {*original, "interval", "payload", "fetchedAt", "fetchError"}):
                    conn.execute("UPDATE csm_rule_providers SET name=?,description=?,config_json=?,updated_at=? WHERE id=?",
                                 (item["name"], item["description"], _json(item["config"]), now, item["id"]))
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


def _legacy_tool_id() -> str:
    # Kept split so the retired product name never appears in source or logs.
    return "c" + "lash_subscription_manager"


def _migrate_legacy_tool_storage(user_id: str) -> None:
    tools_root = get_settings().storage_dir / "user_data" / user_id / "tools"
    previous = tools_root / _legacy_tool_id()
    current = tools_root / TOOL_ID
    if previous.exists() and not current.exists():
        tools_root.mkdir(parents=True, exist_ok=True)
        previous.rename(current)


def migrate_tool_identity() -> None:
    """Carry access policy forward before the renamed tool is exposed."""
    previous_id = _legacy_tool_id()
    with connection_context() as conn:
        platform_tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "platform_tool_visibility" in platform_tables:
            conn.execute("""INSERT OR IGNORE INTO platform_tool_visibility(tool_id,global_public,updated_at)
              SELECT ?,global_public,updated_at FROM platform_tool_visibility WHERE tool_id=?""", (TOOL_ID, previous_id))
            conn.execute("DELETE FROM platform_tool_visibility WHERE tool_id=?", (previous_id,))
        if "platform_tool_user_access" in platform_tables:
            conn.execute("""INSERT OR IGNORE INTO platform_tool_user_access(tool_id,user_id,granted_at)
              SELECT ?,user_id,granted_at FROM platform_tool_user_access WHERE tool_id=?""", (TOOL_ID, previous_id))
            conn.execute("DELETE FROM platform_tool_user_access WHERE tool_id=?", (previous_id,))


def init_database(user_id: str) -> None:
    """Create a user's isolated data store and the global token lookup table."""
    with _init_lock:
        if user_id in _initialized:
            return
        _migrate_legacy_tool_storage(user_id)
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
              alias TEXT NOT NULL DEFAULT '', is_custom INTEGER NOT NULL DEFAULT 0,
              resolved_ip TEXT NOT NULL DEFAULT '', country_code TEXT NOT NULL DEFAULT '',
              country_label TEXT NOT NULL DEFAULT '', geo_checked_at TEXT, geo_error TEXT NOT NULL DEFAULT '',
              country_override_code TEXT NOT NULL DEFAULT '', country_override_label TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS csm_ip_geo_cache (
              ip TEXT PRIMARY KEY, country_code TEXT NOT NULL DEFAULT '',
              country_label TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'success',
              error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_node_sources (
              node_id TEXT NOT NULL, source_id TEXT NOT NULL, source_alias TEXT NOT NULL DEFAULT '',
              last_seen_at TEXT NOT NULL, PRIMARY KEY(node_id, source_id),
              FOREIGN KEY(node_id) REFERENCES csm_nodes(id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES csm_sources(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS csm_profiles (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, target_kernel TEXT NOT NULL DEFAULT 'airplay',
              settings_json TEXT NOT NULL DEFAULT '{}', rule_set_id TEXT, token_encrypted TEXT NOT NULL,
              access_password_salt TEXT NOT NULL DEFAULT '', access_password_hash TEXT NOT NULL DEFAULT '',
              public_access_until TEXT,
              published_at TEXT, published_status TEXT NOT NULL DEFAULT 'draft', last_validation_json TEXT NOT NULL DEFAULT '[]',
              refresh_seconds INTEGER NOT NULL DEFAULT 21600, next_refresh_at TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS csm_settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
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
            CREATE TABLE IF NOT EXISTS csm_profile_refresh_runs (
              id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, status TEXT NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT, duration_ms INTEGER,
              error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              FOREIGN KEY(profile_id) REFERENCES csm_profiles(id) ON DELETE CASCADE);
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
            CREATE INDEX IF NOT EXISTS csm_profile_runs_profile ON csm_profile_refresh_runs(profile_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS csm_subscription_requests_profile ON csm_subscription_requests(profile_id, requested_at DESC);
            """)
            # Lightweight, idempotent migrations for databases created by an
            # earlier version of the tool.
            node_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_nodes)")}
            if "alias" not in node_columns:
                conn.execute("ALTER TABLE csm_nodes ADD COLUMN alias TEXT NOT NULL DEFAULT ''")
            if "is_custom" not in node_columns:
                conn.execute("ALTER TABLE csm_nodes ADD COLUMN is_custom INTEGER NOT NULL DEFAULT 0")
            for column, definition in (
                ("resolved_ip", "TEXT NOT NULL DEFAULT ''"),
                ("country_code", "TEXT NOT NULL DEFAULT ''"),
                ("country_label", "TEXT NOT NULL DEFAULT ''"),
                ("geo_checked_at", "TEXT"),
                ("geo_error", "TEXT NOT NULL DEFAULT ''"),
                ("country_override_code", "TEXT NOT NULL DEFAULT ''"),
                ("country_override_label", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in node_columns:
                    conn.execute(f"ALTER TABLE csm_nodes ADD COLUMN {column} {definition}")
            profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_profiles)")}
            if "refresh_seconds" not in profile_columns:
                conn.execute("ALTER TABLE csm_profiles ADD COLUMN refresh_seconds INTEGER NOT NULL DEFAULT 21600")
            if "next_refresh_at" not in profile_columns:
                conn.execute("ALTER TABLE csm_profiles ADD COLUMN next_refresh_at TEXT")
            for column, definition in (
                ("access_password_salt", "TEXT NOT NULL DEFAULT ''"),
                ("access_password_hash", "TEXT NOT NULL DEFAULT ''"),
                ("public_access_until", "TEXT"),
            ):
                if column not in profile_columns:
                    conn.execute(f"ALTER TABLE csm_profiles ADD COLUMN {column} {definition}")
            # Migrate legacy pools that could contain multiple subscription
            # nodes with the same name. Keep the newest material; aliases are
            # annotations and do not affect this identity rule.
            duplicate_names = conn.execute("""SELECT name FROM csm_nodes
              WHERE is_custom=0 GROUP BY name HAVING COUNT(*)>1""").fetchall()
            for duplicate in duplicate_names:
                rows = conn.execute("SELECT id FROM csm_nodes WHERE is_custom=0 AND name=? ORDER BY last_seen_at DESC,rowid DESC", (duplicate["name"],)).fetchall()
                for old in rows[1:]:
                    conn.execute("DELETE FROM csm_probe_results WHERE node_id=?", (old["id"],))
                    conn.execute("DELETE FROM csm_node_sources WHERE node_id=?", (old["id"],))
                    conn.execute("DELETE FROM csm_nodes WHERE id=?", (old["id"],))
            cache_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_ip_geo_cache)")}
            if "created_at" not in cache_columns:
                if "checked_at" in cache_columns:
                    conn.execute("ALTER TABLE csm_ip_geo_cache RENAME COLUMN checked_at TO created_at")
                else:
                    conn.execute("ALTER TABLE csm_ip_geo_cache ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
            rule_columns = {row["name"] for row in conn.execute("PRAGMA table_info(csm_rule_sets)")}
            if "group_name" not in rule_columns:
                conn.execute("ALTER TABLE csm_rule_sets ADD COLUMN group_name TEXT NOT NULL DEFAULT '默认分组'")
            placeholders = ",".join("?" for _ in AIRPLAY_PROXY_TYPES)
            conn.execute(f"UPDATE csm_nodes SET supported_output=CASE WHEN lower(protocol) IN ({placeholders}) THEN 1 ELSE 0 END", tuple(sorted(AIRPLAY_PROXY_TYPES)))
            conn.execute("UPDATE csm_profiles SET target_kernel='airplay' WHERE target_kernel<>'airplay'")
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
        migrate_tool_identity()
        _initialized.add(user_id)


def _row_source(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "url": _redact_url(_decrypt(row["url_encrypted"])),
            "userAgent": row["user_agent"], "refreshSeconds": row["refresh_seconds"], "enabled": bool(row["enabled"]),
            "status": row["status"], "lastSuccessAt": row["last_success_at"], "lastAttemptAt": row["last_attempt_at"],
            "nextRefreshAt": row["next_refresh_at"], "lastError": row["last_error"], "createdAt": row["created_at"]}


def _default_auto_update_settings() -> dict[str, int]:
    return {
        "sourceRefreshSeconds": DEFAULT_REFRESH_SECONDS,
        "ruleProviderRefreshSeconds": DEFAULT_RULE_PROVIDER_REFRESH_SECONDS,
        "nodeProbeSeconds": DEFAULT_NODE_PROBE_SECONDS,
        "profileRefreshSeconds": DEFAULT_PROFILE_REFRESH_SECONDS,
    }


_AUTO_UPDATE_LAST_RUN_KEYS = {
    "sourceRefreshSeconds": "source_run_at",
    "ruleProviderRefreshSeconds": "rule_provider_run_at",
    "nodeProbeSeconds": "node_probe_run_at",
    "profileRefreshSeconds": "profile_run_at",
}


def _set_auto_update_last_run(user_id: str, setting_key: str, when: str | None = None) -> None:
    raw_key = _AUTO_UPDATE_LAST_RUN_KEYS.get(setting_key)
    if not raw_key:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.execute("""INSERT INTO csm_settings(key,value,updated_at) VALUES(?,?,?)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                     (f"auto_update_last_{raw_key}", when or _now(), _now()))


def auto_update_settings(user: User) -> dict[str, Any]:
    init_database(user.id)
    settings: dict[str, Any] = _default_auto_update_settings()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        for row in conn.execute("SELECT key,value FROM csm_settings WHERE key LIKE 'auto_update_%'"):
            try:
                key = row["key"][len("auto_update_"):]
                if key in settings:
                    settings[key] = max(60, min(7 * 86400, int(row["value"])))
            except (TypeError, ValueError):
                continue
    settings["lastRunAt"] = {key: None for key in _AUTO_UPDATE_LAST_RUN_KEYS}
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        for setting_key, raw_key in _AUTO_UPDATE_LAST_RUN_KEYS.items():
            row = conn.execute("SELECT value FROM csm_settings WHERE key=?", (f"auto_update_last_{raw_key}",)).fetchone()
            if row:
                settings["lastRunAt"][setting_key] = row["value"]
        # Preserve the timestamp written by the earlier unified probe scheduler.
        legacy_probe = conn.execute("SELECT value FROM csm_settings WHERE key='auto_update_last_probe_at'").fetchone()
        if legacy_probe and not settings["lastRunAt"]["nodeProbeSeconds"]:
            settings["lastRunAt"]["nodeProbeSeconds"] = legacy_probe["value"]
    return settings


def save_auto_update_settings(data: dict[str, Any], user: User) -> dict[str, Any]:
    clean = {key: max(60, min(7 * 86400, int(data.get(key, value)))) for key, value in _default_auto_update_settings().items()}
    now = _now()
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        for key, value in clean.items():
            conn.execute("""INSERT INTO csm_settings(key,value,updated_at) VALUES(?,?,?)
              ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                         (f"auto_update_{key}", str(value), now))
        conn.execute("UPDATE csm_sources SET refresh_seconds=? WHERE enabled=1", (clean["sourceRefreshSeconds"],))
        rows = conn.execute("SELECT id,config_json FROM csm_rule_providers").fetchall()
        for row in rows:
            config = _loads(row["config_json"], {})
            if _rule_provider_source_url(config):
                config["interval"] = clean["ruleProviderRefreshSeconds"]
                conn.execute("UPDATE csm_rule_providers SET config_json=?,updated_at=? WHERE id=?", (_json(config), now, row["id"]))
        conn.execute("UPDATE csm_profiles SET refresh_seconds=?", (clean["profileRefreshSeconds"],))
    return auto_update_settings(user)


def list_sources(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_row_source(r) for r in conn.execute("SELECT * FROM csm_sources ORDER BY created_at DESC")]


def create_source(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    url = _require_http_url(str(data.get("url", "")))
    name = str(data.get("name") or _redact_url(url))[:120]
    refresh_seconds = max(60, min(7 * 86400, int(data.get("refreshSeconds") or auto_update_settings(user)["sourceRefreshSeconds"])))
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
            "server": server, "port": port, "config": config, "supported_output": supported and protocol in AIRPLAY_PROXY_TYPES}


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


def proxy_share_uri(proxy: dict[str, Any]) -> str | None:
    """Convert a published AirPlay proxy into a broadly compatible share URI."""
    kind = str(proxy.get("type") or "").lower()
    server = str(proxy.get("server") or "").strip()
    try:
        port = int(proxy.get("port"))
    except (TypeError, ValueError):
        return None
    if not server or not 0 < port < 65536 or kind not in PARSEABLE_URIS:
        return None
    name = str(proxy.get("name") or kind)
    fragment = quote(name, safe="")
    host = f"[{server}]" if ":" in server and not server.startswith("[") else server

    if kind == "ss":
        cipher, password = str(proxy.get("cipher") or ""), str(proxy.get("password") or "")
        if not cipher or not password:
            return None
        credentials = base64.urlsafe_b64encode(f"{cipher}:{password}@{host}:{port}".encode()).decode().rstrip("=")
        plugin = str(proxy.get("plugin") or "")
        query = f"?{urlencode({'plugin': plugin})}" if plugin else ""
        return f"ss://{credentials}{query}#{fragment}"
    if kind == "vmess":
        payload = {
            "v": "2", "ps": name, "add": server, "port": str(port), "id": str(proxy.get("uuid") or ""),
            "aid": str(proxy.get("alterId") or 0), "scy": str(proxy.get("cipher") or "auto"),
            "net": str(proxy.get("network") or "tcp"), "type": "none", "host": "", "path": "", "tls": str(proxy.get("tls") or ""),
            "sni": str(proxy.get("servername") or proxy.get("sni") or ""),
        }
        if not payload["id"]:
            return None
        ws_options = proxy.get("ws-opts") if isinstance(proxy.get("ws-opts"), dict) else {}
        payload["path"] = str(ws_options.get("path") or "")
        headers = ws_options.get("headers") if isinstance(ws_options.get("headers"), dict) else {}
        payload["host"] = str(headers.get("Host") or "")
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode()
        return f"vmess://{encoded}"

    identity = str(proxy.get("uuid") or proxy.get("password") or proxy.get("auth-str") or "")
    if not identity:
        return None
    query: dict[str, str] = {}
    for source, target in (("sni", "sni"), ("servername", "sni"), ("network", "type"), ("flow", "flow"), ("client-fingerprint", "fp"), ("packet-encoding", "packetEncoding"), ("obfs", "obfs"), ("obfs-password", "obfs-password"), ("up", "up"), ("down", "down"), ("congestion-controller", "congestion_control")):
        value = proxy.get(source)
        if value not in (None, ""):
            query[target] = str(value)
    if kind == "trojan":
        query.setdefault("security", "tls")
    elif kind == "vless":
        reality = proxy.get("reality-opts") if isinstance(proxy.get("reality-opts"), dict) else {}
        if reality.get("public-key"):
            query["security"] = "reality"
            query["pbk"] = str(reality["public-key"])
            if reality.get("short-id"):
                query["sid"] = str(reality["short-id"])
        elif proxy.get("tls"):
            query["security"] = str(proxy["tls"])
    elif kind in {"hysteria", "hysteria2", "tuic"}:
        query.setdefault("sni", str(proxy.get("sni") or proxy.get("servername") or ""))
    query = {key: value for key, value in query.items() if value}
    return f"{kind}://{quote(identity, safe='')}@{host}:{port}" + (f"?{urlencode(query)}" if query else "") + f"#{fragment}"


def _airplay_node(value: dict[str, Any]) -> dict[str, Any] | None:
    protocol = str(value.get("type") or "").lower()
    if not protocol: return None
    server = str(value.get("server") or "")
    try: port = int(value.get("port")) if value.get("port") is not None else None
    except (ValueError, TypeError): port = None
    supported = protocol in AIRPLAY_PROXY_TYPES
    return _node(str(value.get("name") or protocol), protocol, server, port, dict(value), supported)


def parse_subscription(content: bytes | str) -> tuple[list[dict[str, Any]], int]:
    """Accept AirPlay YAML, provider payload, base64 subscriptions and URI lines."""
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
                    parsed = _airplay_node(proxy)
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
    headers = {"User-Agent": user_agent or "pansis-airplay-subscription-manager/1.0"}
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


def _preserved_geo_fields(conn: sqlite3.Connection, node_id: str, server: str) -> tuple[str, str, str, str | None, str]:
    """Keep GeoIP data when a refreshed node still points at the same server."""
    row = conn.execute(
        "SELECT server,resolved_ip,country_code,country_label,geo_checked_at,geo_error FROM csm_nodes WHERE id=?",
        (node_id,),
    ).fetchone()
    if row and str(row["server"] or "") == str(server or ""):
        return (str(row["resolved_ip"] or ""), str(row["country_code"] or ""), str(row["country_label"] or ""),
                row["geo_checked_at"], str(row["geo_error"] or ""))
    return ("", "", "", None, "")


def _store_nodes(conn: sqlite3.Connection, source_id: str, nodes: Iterable[dict[str, Any]]) -> int:
    now = _now(); count = 0
    for item in nodes:
        # Subscription nodes are identified by their source name.  When an
        # upstream refresh contains the same name again, its material is the
        # newest version and replaces the previous node.  Aliases are only
        # display annotations and never participate in identity.
        row = conn.execute("SELECT * FROM csm_nodes WHERE name=? AND is_custom=0 ORDER BY last_seen_at DESC LIMIT 1", (item["name"],)).fetchone()
        if row:
            node_id = row["id"]
            geo = _preserved_geo_fields(conn, node_id, item["server"])
            conn.execute("""UPDATE csm_nodes SET fingerprint=?,name=?,protocol=?,server=?,port=?,config_json=?,supported_output=?,
              last_seen_at=?,resolved_ip=?,country_code=?,country_label=?,geo_checked_at=?,geo_error=? WHERE id=?""",
              (item["fingerprint"], item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]),
               int(item["supported_output"]), now, *geo, node_id))
        else:
            node_id = _id()
            stored_fingerprint = item["fingerprint"]
            if conn.execute("SELECT 1 FROM csm_nodes WHERE fingerprint=?", (stored_fingerprint,)).fetchone():
                stored_fingerprint = _hash({"subscriptionName": item["name"], "material": stored_fingerprint})
            conn.execute("""INSERT INTO csm_nodes(
              id,stable_identity,fingerprint,name,protocol,server,port,config_json,
              supported_output,first_seen_at,last_seen_at,
              resolved_ip,country_code,country_label,geo_checked_at,geo_error)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (node_id, _hash({"subscriptionName": item["name"]}), stored_fingerprint, item["name"], item["protocol"], item["server"], item["port"], _json(item["config"]), int(item["supported_output"]), now, now, "", "", "", None, ""))
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
        _start_geoip_refresh(user)
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


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _geo_retry_allowed(checked_at: str | None) -> bool:
    checked = _parse_utc(checked_at)
    if not checked:
        return True
    return datetime.now(timezone.utc) - checked >= timedelta(seconds=GEOIP_FAILURE_RETRY_SECONDS)


def _public_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    return address if address.is_global else None


def _resolve_node_ip(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, str]:
    """Resolve the public endpoint IP used for a node's GeoIP lookup."""
    server = str(row["server"] or "").strip()
    direct = _public_ip_address(server)
    if direct:
        return str(direct), ""
    try:
        ipaddress.ip_address(server)
    except ValueError:
        pass
    else:
        return "", "节点服务器地址是保留地址，无法识别国家/地区。"

    probe = conn.execute(
        "SELECT dns_address FROM csm_probe_results WHERE node_id=? ORDER BY created_at DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    probe_error = ""
    if probe:
        probed = _public_ip_address(probe["dns_address"])
        if probed:
            return str(probed), ""
        if str(probe["dns_address"] or "").strip():
            # TUN/fake-IP DNS can leave a reserved address in the probe log.
            # Do not let that stale result prevent a fresh hostname lookup.
            probe_error = "节点最近探测到的是保留地址。"

    if not server:
        return "", "节点服务器地址为空，无法识别国家/地区。"
    try:
        port = int(row["port"] or 0) or None
        addresses = socket.getaddrinfo(server.rstrip("."), port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        prefix = f"{probe_error} " if probe_error else ""
        return "", f"{prefix}节点域名解析失败：{str(exc)[:160]}"
    # Prefer IPv4 because public GeoIP services and many proxy endpoints have
    # more stable country attribution for A records. Fall back to public IPv6.
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _, _, _, _, sockaddr in addresses:
        probed = _public_ip_address(sockaddr[0])
        if probed:
            candidates.append(probed)
    if candidates:
        candidates.sort(key=lambda address: address.version)
        return str(candidates[0]), ""
    prefix = f"{probe_error} " if probe_error else ""
    return "", f"{prefix}节点域名未解析到公网 IP，无法识别国家/地区。"


def _query_ip_sb(ip: str) -> dict[str, str]:
    """Query IP.SB with a process-wide request-start rate limit."""
    global _geoip_last_request_at
    with _geoip_request_lock:
        wait = GEOIP_MIN_REQUEST_INTERVAL - (time.monotonic() - _geoip_last_request_at)
        if wait > 0:
            time.sleep(wait)
        _geoip_last_request_at = time.monotonic()
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
                          headers={"User-Agent": GEOIP_USER_AGENT, "Accept": "application/json"}) as client:
            response = client.get(f"{GEOIP_API_BASE}/{ip}")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"countryCode": "", "countryLabel": "", "error": f"IP.SB 查询失败：{str(exc)[:160]}"}
    return _normalise_ip_sb_payload(payload)


def _normalise_ip_sb_payload(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"countryCode": "", "countryLabel": "", "error": "IP.SB 返回了无效的国家/地区数据。"}
    code = str(payload.get("country_code") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        return {"countryCode": "", "countryLabel": "", "error": "IP.SB 未返回有效的国家/地区代码。"}
    label = COUNTRY_LABELS.get(code, str(payload.get("country") or code).strip() or code)
    return {"countryCode": code, "countryLabel": label, "error": ""}


def _cache_geoip(conn: sqlite3.Connection, ip: str, result: dict[str, str], now: str) -> None:
    status = "success" if result.get("countryCode") else "failed"
    conn.execute("""INSERT INTO csm_ip_geo_cache(
      ip,country_code,country_label,status,error,created_at)
      VALUES(?,?,?,?,?,?)
      ON CONFLICT(ip) DO UPDATE SET country_code=excluded.country_code,
        country_label=excluded.country_label,status=excluded.status,
        error=excluded.error,created_at=excluded.created_at""",
      (ip, result.get("countryCode", ""), result.get("countryLabel", ""),
       status, result.get("error", "")[:240], now))


def _update_node_geoip(conn: sqlite3.Connection, node_id: str, ip: str, result: dict[str, str], now: str) -> None:
    conn.execute("""UPDATE csm_nodes SET resolved_ip=?,country_code=?,country_label=?,
      geo_checked_at=?,geo_error=? WHERE id=?""",
      (ip, result.get("countryCode", ""), result.get("countryLabel", ""), now,
       result.get("error", "")[:240], node_id))


def _refresh_node_geoip(node_ids: list[str], user: User) -> tuple[list[dict[str, Any]], bool]:
    """Resolve GeoIP for explicit nodes, or all nodes when no ID is supplied."""
    init_database(user.id)
    ids = list(dict.fromkeys(str(value) for value in node_ids if value))[:GEOIP_MAX_NODES]
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(f"SELECT * FROM csm_nodes WHERE id IN ({marks})", ids).fetchall()
        else:
            rows = conn.execute("SELECT * FROM csm_nodes ORDER BY last_seen_at DESC LIMIT ?", (GEOIP_MAX_NODES,)).fetchall()

        now = _now(); changed = False; pending: list[tuple[sqlite3.Row, str]] = []
        for row in rows:
            ip, error = _resolve_node_ip(conn, row)
            if not ip:
                if str(row["resolved_ip"] or "") or str(row["country_code"] or "") or str(row["geo_error"] or "") != error:
                    _update_node_geoip(conn, row["id"], "", {"countryCode": "", "countryLabel": "", "error": error}, now)
                    changed = True
                continue

            cache = conn.execute("SELECT * FROM csm_ip_geo_cache WHERE ip=?", (ip,)).fetchone()
            if cache and cache["status"] == "success":
                result = {"countryCode": str(cache["country_code"] or ""), "countryLabel": str(cache["country_label"] or ""), "error": ""}
                if (str(row["resolved_ip"] or "") != ip or str(row["country_code"] or "") != result["countryCode"]
                        or str(row["country_label"] or "") != result["countryLabel"] or str(row["geo_error"] or "")):
                    _update_node_geoip(conn, row["id"], ip, result, str(cache["created_at"] or now))
                    changed = True
                continue

            same_ip = str(row["resolved_ip"] or "") == ip
            if same_ip and str(row["country_code"] or "") and not str(row["geo_error"] or ""):
                continue
            if cache and cache["status"] == "failed" and not _geo_retry_allowed(str(cache["created_at"] or "")):
                result = {"countryCode": "", "countryLabel": "", "error": str(cache["error"] or "IP.SB 查询失败")}
                if str(row["geo_error"] or "") != result["error"]:
                    _update_node_geoip(conn, row["id"], ip, result, str(cache["created_at"] or now))
                    changed = True
                continue
            if same_ip and str(row["geo_error"] or "") and not _geo_retry_allowed(str(row["geo_checked_at"] or "")):
                continue
            pending.append((row, ip))

    pending_ips = list(dict.fromkeys(ip for _row, ip in pending))
    if pending_ips:
        results = {ip: _query_ip_sb(ip) for ip in pending_ips}
        now = _now()
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            for row, ip in pending:
                result = results[ip]
                _cache_geoip(conn, ip, result, now)
                _update_node_geoip(conn, row["id"], ip, result, now)
                changed = True

    result_ids = [row["id"] for row in rows]
    if not result_ids:
        return [], changed
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        result_nodes = list_nodes(user)
    return [node for node in result_nodes if node["id"] in set(result_ids)], changed


def refresh_node_geoip(node_ids: list[str], user: User) -> list[dict[str, Any]]:
    return _refresh_node_geoip(node_ids, user)[0]


def _start_geoip_refresh(user: User) -> None:
    """Run GeoIP resolution in the background without blocking a refresh."""
    with _geoip_job_lock:
        if user.id in _geoip_job_users:
            return
        _geoip_job_users.add(user.id)

    def run() -> None:
        try:
            _nodes, changed = _refresh_node_geoip([], user)
            if changed:
                _rebuild_changed_published_profiles(user)
        except Exception:
            # GeoIP is an enhancement: it must never break subscription refresh
            # or the global probe scheduler.
            pass
        finally:
            with _geoip_job_lock:
                _geoip_job_users.discard(user.id)

    threading.Thread(target=run, name=f"csm-geoip-{user.id}", daemon=True).start()


def _node_dict(row: sqlite3.Row, source_names: list[str] | None = None, probe: sqlite3.Row | None = None,
               source_ids: list[str] | None = None) -> dict[str, Any]:
    alias = str(row["alias"] or "")
    automatic_country = str(row["country_code"] or "")
    automatic_country_label = str(row["country_label"] or "")
    override_country = str(row["country_override_code"] or "")
    override_country_label = str(row["country_override_label"] or "")
    country = override_country or automatic_country
    country_label = override_country_label or (automatic_country_label if not override_country else COUNTRY_LABELS.get(override_country, override_country))
    return {"id": row["id"], "stableIdentity": row["stable_identity"], "name": row["name"], "alias": alias,
            "displayName": alias or row["name"], "protocol": row["protocol"], "server": row["server"], "port": row["port"],
            "config": _loads(row["config_json"], {}), "supportedOutput": _compatible(str(row["protocol"])),
            "isCustom": bool(row["is_custom"]), "lastSeenAt": row["last_seen_at"], "sources": source_names or [],
            "sourceIds": source_ids or [], "country": country or None, "countryLabel": country_label or country or None,
            "automaticCountry": automatic_country or None,
            "automaticCountryLabel": automatic_country_label or automatic_country or None,
            "countryOverride": override_country or None,
            "countryOverrideLabel": override_country_label or override_country or None,
            "resolvedIp": str(row["resolved_ip"] or ""), "geoError": str(row["geo_error"] or ""),
            "geoCheckedAt": row["geo_checked_at"],
            "tcp": None if not probe else {
                "reachable": bool(probe["reachable"]), "latencyMs": probe["latency_ms"], "error": probe["error"], "checkedAt": probe["created_at"]}}


def _normalise_custom_node(content: str) -> dict[str, Any]:
    """Validate one user-authored AirPlay proxy mapping."""
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
    if protocol not in AIRPLAY_PROXY_TYPES:
        raise ToolboxError("INVALID_CUSTOM_NODE_TYPE", "节点协议不受 AirPlay 支持。", status_code=422)
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
        geo = _preserved_geo_fields(conn, node_id, material["server"])
        conn.execute("""UPDATE csm_nodes SET fingerprint=?,name=?,protocol=?,server=?,port=?,
                      config_json=?,supported_output=1,last_seen_at=?,alias=?,
                      resolved_ip=?,country_code=?,country_label=?,geo_checked_at=?,geo_error=? WHERE id=?""", (
            fingerprint, material["name"], material["protocol"], material["server"],
            material["port"], _json(material["config"]), _now(), clean_alias, *geo, node_id))
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
            raise ToolboxError("INVALID_CUSTOM_NODE_TYPE", "该订阅节点的协议不受 AirPlay 支持，不能复制为自定义节点。", status_code=422)
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
        updated = _node_dict(conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone())
    _rebuild_changed_published_profiles(user)
    return updated


def update_node_country_override(node_id: str, country_code: str, country_label: str, user: User) -> dict[str, Any]:
    """Set or clear a manual country override without changing GeoIP data."""
    init_database(user.id)
    code = str(country_code or "").strip().upper()
    if code and not re.fullmatch(r"[A-Z]{2}", code):
        raise ToolboxError("INVALID_COUNTRY_CODE", "国家/地区代码必须是两个英文字母。", status_code=422)
    label = str(country_label or "").strip()[:80]
    if code:
        label = COUNTRY_LABELS.get(code, label or code)
    else:
        label = ""
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT id FROM csm_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            _not_found("节点")
        conn.execute(
            "UPDATE csm_nodes SET country_override_code=?,country_override_label=? WHERE id=?",
            (code, label, node_id),
        )
        updated = _node_dict(conn.execute("SELECT * FROM csm_nodes WHERE id=?", (node_id,)).fetchone())
    _rebuild_changed_published_profiles(user)
    return updated


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
    return str(row["name"] or "")


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
        return [_node_ref(row) for row in rows if str(row["country_override_code"] or row["country_code"] or "") in countries]
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
    return protocol.lower() in AIRPLAY_PROXY_TYPES


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
        "passwordRequired": bool(row["access_password_hash"]),
        "publicAccessUntil": row["public_access_until"],
        "validation": _loads(row["last_validation_json"], []), "subscriptionToken": public_token,
        "createdAt": row["created_at"], "updatedAt": row["updated_at"], "refreshSeconds": row["refresh_seconds"], "nextRefreshAt": row["next_refresh_at"],
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
    refresh_seconds = max(60, min(7 * 86400, int(data.get("refreshSeconds") or auto_update_settings(user)["profileRefreshSeconds"])))
    password_required = bool(data.get("passwordRequired", True))
    password = str(data.get("accessPassword") or "")
    if password_required and not 4 <= len(password) <= 128:
        raise ToolboxError("PROFILE_PASSWORD_REQUIRED", "访问密码长度必须为 4 到 128 个字符。", status_code=422)
    password_salt = secrets.token_hex(16) if password_required else ""
    password_digest = hash_password(password, password_salt) if password_required else ""
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("""INSERT INTO csm_profiles(
          id,name,target_kernel,settings_json,rule_set_id,token_encrypted,published_at,
          published_status,last_validation_json,refresh_seconds,next_refresh_at,created_at,updated_at,
          access_password_salt,access_password_hash,public_access_until)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (profile_id, str(data.get("name") or "未命名聚合")[:120], "airplay", _json(_profile_settings(data.get("settings"))), data.get("ruleSetId"), _encrypt(token), None, "draft", "[]", refresh_seconds, None, now, now, password_salt, password_digest, None))
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
        if "passwordRequired" in data:
            password_required = bool(data["passwordRequired"])
            password = str(data.get("accessPassword") or "")
            if password_required and not row["access_password_hash"] and not 4 <= len(password) <= 128:
                raise ToolboxError("PROFILE_PASSWORD_REQUIRED", "启用密码访问时，请设置 4 到 128 个字符的密码。", status_code=422)
            if password_required and password:
                if not 4 <= len(password) <= 128:
                    raise ToolboxError("INVALID_PROFILE_PASSWORD", "访问密码长度必须为 4 到 128 个字符。", status_code=422)
                salt = secrets.token_hex(16)
                changed.update({"access_password_salt": salt, "access_password_hash": hash_password(password, salt), "public_access_until": None})
            elif not password_required:
                changed.update({"access_password_salt": "", "access_password_hash": "", "public_access_until": None})
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


def _rule_provider_source_url(config: dict[str, Any]) -> str:
    provider_type = str(config.get("type") or "").strip().lower()
    if provider_type == "http":
        return str(config.get("url") or "").strip()
    if provider_type == "cached":
        return str(config.get("sourceUrl") or config.get("url") or "").strip()
    return ""


def _attach_rule_provider_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Persist a remote payload so public reads never perform network I/O."""
    url = _rule_provider_source_url(config)
    if not url:
        return config
    payload = _download_rule_provider_payload(url)
    return {**config, "payload": payload, "fetchedAt": _now(), "fetchError": ""}


def _rule_provider_snapshot_payload(config: dict[str, Any]) -> list[str]:
    return [str(item) for item in config.get("payload") or [] if str(item).strip()]


def _rule_provider_refresh_due(config: dict[str, Any], now: datetime) -> bool:
    if not _rule_provider_source_url(config):
        return False
    try:
        interval = max(60, int(config.get("interval") or 86400))
    except (TypeError, ValueError):
        interval = 86400
    fetched_at = str(config.get("fetchedAt") or "")
    if not fetched_at:
        return True
    try:
        parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now >= parsed + timedelta(seconds=interval)


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
        raise ToolboxError("INCOMPATIBLE_PROVIDER_FORMAT", "MRS 格式不在当前 AirPlay YAML 输出范围内，请改用 YAML Provider。", status_code=422)
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
        if provider_type == "http" and config.get("url"):
            config = _attach_rule_provider_snapshot(config)
    else:
        raise ToolboxError("INVALID_PROVIDER_TYPE", "Rule Provider 仅支持规则订阅、http、file 或自定义规则。", status_code=422)
    return name, provider_key, description, config


def save_rule_provider(data: dict[str, Any], user: User, provider_id: str | None = None) -> dict[str, Any]:
    init_database(user.id)
    name, provider_key, description, config = _normalise_rule_provider(data)
    if _rule_provider_source_url(config):
        config["interval"] = auto_update_settings(user)["ruleProviderRefreshSeconds"]
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
    """Expand a Provider from its locally scheduled snapshot, never on demand."""
    value = _normalise_legacy_manual_provider(config)
    url = str(value.get("url") or value.get("sourceUrl") or "").strip()
    if str(value.get("type") or "").lower() not in {"http", "cached"} or not url:
        raise ToolboxError(
            "RULE_PROVIDER_INLINE_REQUIRES_URL",
            "拉取后优先仅支持带 URL 的规则订阅；请改为 URL 优先，或为该 Rule Provider 配置 URL。",
            status_code=422,
        )
    payload = _rule_provider_snapshot_payload(value)
    if not payload:
        raise ToolboxError(
            "RULE_PROVIDER_SNAPSHOT_NOT_READY",
            "规则快照尚未生成，等待定时拉取后再试。",
            status_code=422,
        )
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
            if reference == str(row["name"] or ""):
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
            messages.append({"level": "error", "code": "INCOMPATIBLE_OUTPUT", "message": f"节点 {label}（{item['protocol']}）不受 AirPlay 支持。"})
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
                messages.append({"level": "error", "code": "AMBIGUOUS_NODE_REFERENCE", "message": f"策略组 {name} 引用了重名节点：{ref}，请先确保节点名称唯一。"})
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
            messages.append({"level": "error", "code": "INCOMPATIBLE_RULE", "message": "仅支持 AirPlay 的字符串规则。"})
            continue
        rule_type = rule.split(",", 1)[0].strip().upper()
        if rule_type not in AIRPLAY_RULE_TYPES:
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
    value = {key: item for key, item in config.items() if key in allowed and item not in (None, "", [], {})}
    return _normalise_proxy_option_types(value, protocol)


def _normalise_proxy_option_types(value: dict[str, Any], protocol: str) -> dict[str, Any]:
    value = dict(value)
    for key in {"tls", "udp", "skip-cert-verify", "disable-sni", "reduce-rtt"} & value.keys():
        item = value[key]
        if not isinstance(item, bool):
            text = str(item).strip().lower()
            value[key] = text not in {"", "0", "false", "no", "off", "none"}
    if protocol == "vmess" and "alterId" in value and not isinstance(value["alterId"], bool):
        try:
            value["alterId"] = int(value["alterId"])
        except (TypeError, ValueError):
            pass
    return value


def _normalise_published_content(content: str) -> str:
    """Repair type-invalid options in snapshots created before output normalisation."""
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return content
    if not isinstance(document, dict) or not isinstance(document.get("proxies"), list):
        return content
    changed = False
    proxies: list[Any] = []
    for item in document["proxies"]:
        if not isinstance(item, dict):
            proxies.append(item)
            continue
        normalised = _normalise_proxy_option_types(item, str(item.get("type") or "").lower())
        proxies.append(normalised)
        changed = changed or normalised != item
    if not changed:
        return content
    document["proxies"] = proxies
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


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
    """Build proxy names and resolve both original names and aliases."""
    used: dict[str, int] = {}; output = []; candidates: dict[str, set[str]] = {}
    for row in nodes:
        protocol = str(row["protocol"] or "").lower()
        if not _compatible(protocol):
            continue
        value = _proxy_output(_loads(row["config_json"], {}), protocol)
        original = str(row["name"] or value.get("name") or "")
        alias = str(row["alias"] or "").strip()
        base = f"{alias}-{original}" if alias else original
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


def _build_process_log(
    profile: sqlite3.Row,
    nodes: list[sqlite3.Row],
    rule_set: dict[str, Any] | None,
    config: dict[str, Any],
    messages: list[dict[str, str]],
    content: str,
) -> list[str]:
    """Explain every materialization step used to create the output YAML."""
    settings = _loads(profile["settings_json"], {})
    output_mode = _profile_rule_provider_output_mode(profile)
    lines: list[str] = []

    def add(message: str) -> None:
        lines.append(f"[{len(lines) + 1:03d}] {message}")

    add(f"读取聚合配置「{profile['name']}」，运行模式固定为 rule。")
    add(
        "应用基础设置："
        f"mixed-port={config.get('mixed-port', 7890)}，allow-lan={str(config.get('allow-lan', False)).lower()}，"
        f"ipv6={str(config.get('ipv6', False)).lower()}，DNS={'保留' if config.get('dns') else '未配置'}。"
    )
    rule_set_name = str(rule_set.get("name") or "未关联") if rule_set else "未关联"
    add(f"读取规则组：{rule_set_name}。")
    add(
        "Rule Provider 输出模式："
        + ("拉取后优先，服务端将远程 payload 展开为具体规则。" if output_mode == "inline" else "URL 优先，输出 rule-providers 配置。")
    )

    if rule_set is None:
        add("未找到规则组，输出仅包含基础配置与节点列表。")
    else:
        bindings = (rule_set.get("importMeta") or {}).get("providerBindings")
        if isinstance(bindings, dict) and bindings:
            for group_name, provider_ids in bindings.items():
                if isinstance(provider_ids, list):
                    add(f"策略组「{group_name}」绑定 Rule Provider：{', '.join(map(str, provider_ids)) or '无'}。")
        else:
            add("未发现策略组与 Rule Provider 的绑定。")
        missing_ids = (rule_set.get("importMeta") or {}).get("missingProviderIds") or []
        if missing_ids:
            add(f"发现缺失的 Rule Provider：{', '.join(map(str, missing_ids))}。")

    compatible_nodes = [row for row in nodes if _compatible(str(row["protocol"]))]
    incompatible_nodes = [row for row in nodes if not _compatible(str(row["protocol"]))]
    add(f"规则引用节点 {len(nodes)} 个，其中 AirPlay 可输出 {len(compatible_nodes)} 个，不可输出 {len(incompatible_nodes)} 个。")
    _, references, ambiguous = _node_output_material(nodes)
    for row in nodes:
        original = str(row["name"] or "")
        alias = str(row["alias"] or "")
        output_name = references.get(original, "")
        if not output_name:
            output_name = "（因不兼容或引用不明确被跳过）"
        detail = f"别名「{alias}」" if alias else "未设置别名"
        add(f"节点「{original}」{detail}，协议 {row['protocol']}，输出名称：{output_name}。")
    for reference in sorted(ambiguous):
        add(f"节点引用「{reference}」匹配多个输出节点，已标记为歧义引用。")

    if rule_set is None:
        add("没有策略组和规则可展开。")
    else:
        add("展开节点分组，并将节点分组成员合入策略组成员列表。")
        for group in rule_set.get("groups") or []:
            if not isinstance(group, dict):
                continue
            name = str(group.get("name") or "")
            node_group_ids = [str(item) for item in (group.get("nodeGroups") or []) if item]
            proxies = [str(item) for item in (group.get("proxies") or [])]
            add(
                f"策略组「{name}」类型 {group.get('type') or 'select'}，"
                f"节点分组 {len(node_group_ids)} 个，展开后成员 {len(proxies)} 个：{', '.join(proxies) or '无'}。"
            )

    add(f"生成 YAML：{len(content.encode('utf-8'))} 字节，{len(content.splitlines())} 行。")
    providers = config.get("rule-providers") or {}
    if providers:
        for key, provider in providers.items():
            add(
                f"输出 Rule Provider「{key}」：type={provider.get('type')}，"
                f"behavior={provider.get('behavior')}，url={provider.get('url', '')}，path={provider.get('path', '')}。"
            )
    else:
        add("最终 YAML 不包含 rule-providers（没有远程 Provider，或已按拉取后优先展开为具体规则）。")

    add(f"最终输出策略组 {len(config.get('proxy-groups') or [])} 个，规则 {len(config.get('rules') or [])} 条，节点 {len(config.get('proxies') or [])} 个。")
    add("校验结果：" + ("通过，未发现错误。" if not any(item["level"] == "error" for item in messages) else "未通过。"))
    for item in messages:
        add(f"{item.get('level', 'info').upper()} {item.get('code', 'UNKNOWN')}：{item.get('message', '')}")
    return lines


def preview_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        profile, nodes, rule_set = _profile_material(conn, profile_id)
        messages = _validate_material(profile, nodes, rule_set)
        config = _build_config(profile, nodes, rule_set)
        content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        build_log = _build_process_log(profile, nodes, rule_set, config, messages, content)
    return {"yaml": content, "valid": not any(m["level"] == "error" for m in messages), "messages": messages, "buildLog": build_log}


def publish_profile(profile_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    run_id, started, clock = _id(), _now(), time.monotonic()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("INSERT INTO csm_profile_refresh_runs(id,profile_id,status,started_at,created_at) VALUES(?,?,?,?,?)", (run_id, profile_id, "running", started, started))
    try:
        preview = preview_profile(profile_id, user)
        if not preview["valid"]:
            reason = "；".join(str(item.get("message") or "校验失败") for item in preview["messages"] if item.get("level") == "error")
            raise ToolboxError("PUBLISH_VALIDATION_FAILED", f"配置校验未通过：{reason or '请查看诊断日志'}", status_code=422, extra={"messages": preview["messages"]})
        now = _now()
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            profile = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
            _create_published_snapshot(conn, profile_id, preview["yaml"], now)
            row = conn.execute("SELECT refresh_seconds FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
            refresh_seconds = int(row["refresh_seconds"] or DEFAULT_PROFILE_REFRESH_SECONDS)
            next_refresh = datetime.fromtimestamp(time.time() + refresh_seconds, timezone.utc).isoformat()
            conn.execute("UPDATE csm_profiles SET published_at=?,published_status='published',last_validation_json=?,next_refresh_at=?,updated_at=? WHERE id=?", (now, _json(preview["messages"]), next_refresh, now, profile_id))
            token = _decrypt(profile["token_encrypted"])
            conn.execute("UPDATE csm_profile_refresh_runs SET status='success',finished_at=?,duration_ms=? WHERE id=?", (now, int((time.monotonic() - clock) * 1000), run_id))
        return {"profileId": profile_id, "publishedAt": now, "subscriptionToken": token, "yaml": preview["yaml"]}
    except Exception as exc:
        message = exc.message if isinstance(exc, ToolboxError) else str(exc)
        finished = _now()
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            conn.execute("UPDATE csm_profile_refresh_runs SET status='failed',finished_at=?,duration_ms=?,error=? WHERE id=?", (finished, int((time.monotonic() - clock) * 1000), str(message)[:2000], run_id))
            conn.execute("UPDATE csm_profiles SET published_status='degraded',updated_at=? WHERE id=? AND published_at IS NOT NULL", (finished, profile_id))
        raise


def _create_published_snapshot(conn: sqlite3.Connection, profile_id: str, content: str, now: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS csm_published_snapshots (id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, content_encrypted TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("DELETE FROM csm_published_snapshots WHERE profile_id=?", (profile_id,))
    conn.execute("INSERT INTO csm_published_snapshots VALUES(?,?,?,?,?)", (_id(), profile_id, _encrypt(content), hashlib.sha256(content.encode()).hexdigest(), now))


def _public_profile_location(token: str) -> tuple[str, str] | None:
    if not token or len(token) > 300:
        return None
    with connection_context() as conn:
        row = conn.execute(
            "SELECT user_id,profile_id FROM csm_public_tokens WHERE token_hash=? AND enabled=1",
            (_token_hash(token),),
        ).fetchone()
    return (str(row["user_id"]), str(row["profile_id"])) if row else None


def _public_access_is_open(value: str | None) -> bool:
    deadline = _parse_utc(value)
    return bool(deadline and deadline > datetime.now(timezone.utc))


def public_profile_access(token: str) -> dict[str, Any] | None:
    location = _public_profile_location(token)
    if not location:
        return None
    user_id, profile_id = location
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT access_password_hash,public_access_until FROM csm_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
    if not row:
        return None
    required = bool(row["access_password_hash"])
    return {
        "profileId": profile_id,
        "passwordRequired": required,
        "publicAccessUntil": row["public_access_until"],
        "publicAccessOpen": not required or _public_access_is_open(row["public_access_until"]),
    }


def authenticate_public_profile(token: str, password: str) -> str | None:
    location = _public_profile_location(token)
    if not location:
        return None
    user_id, profile_id = location
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT access_password_salt,access_password_hash FROM csm_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
    if not row or not row["access_password_hash"]:
        return None
    if not verify_password(str(password), str(row["access_password_salt"]), str(row["access_password_hash"])):
        return None
    payload = {
        "tokenHash": _token_hash(token),
        "passwordHash": str(row["access_password_hash"]),
        "expiresAt": int(time.time()) + 12 * 3600,
    }
    return _encrypt(_json(payload))


def public_profile_session_valid(token: str, session_token: str | None) -> bool:
    if not session_token:
        return False
    location = _public_profile_location(token)
    if not location:
        return False
    try:
        payload = _loads(_decrypt(session_token), {})
    except ToolboxError:
        return False
    if (str(payload.get("tokenHash") or "") != _token_hash(token)
            or int(payload.get("expiresAt") or 0) <= int(time.time())):
        return False
    user_id, profile_id = location
    init_database(user_id)
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute("SELECT access_password_hash FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
    return bool(row and row["access_password_hash"] and secrets.compare_digest(
        str(payload.get("passwordHash") or ""), str(row["access_password_hash"])
    ))


def open_public_subscription(token: str, seconds: int = 300) -> str | None:
    location = _public_profile_location(token)
    if not location:
        return None
    user_id, profile_id = location
    init_database(user_id)
    deadline = datetime.fromtimestamp(time.time() + max(1, min(seconds, 300)), timezone.utc).isoformat()
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        row = conn.execute("SELECT access_password_hash FROM csm_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row or not row["access_password_hash"]:
            return None
        conn.execute("UPDATE csm_profiles SET public_access_until=? WHERE id=?", (deadline, profile_id))
    return deadline


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
            profile = conn.execute(
                "SELECT name,access_password_hash,public_access_until FROM csm_profiles WHERE id=?",
                (index["profile_id"],),
            ).fetchone()
            snapshot = conn.execute("SELECT * FROM csm_published_snapshots WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (index["profile_id"],)).fetchone()
            if not profile or not snapshot: return None
            if profile["access_password_hash"] and not _public_access_is_open(profile["public_access_until"]):
                return None
            content = _normalise_published_content(_decrypt(snapshot["content_encrypted"]))
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            try:
                conn.execute(
                    "INSERT INTO csm_subscription_requests VALUES(?,?,?,?,?,?)",
                    (_id(), index["profile_id"], _now(), client_ip[:64], user_agent[:300], 200),
                )
            except sqlite3.Error:
                pass
            return content, content_hash, snapshot["created_at"], profile["name"]
    except (sqlite3.Error, ToolboxError): return None


def _latest_node_probes(conn: sqlite3.Connection) -> tuple[dict[str, list[sqlite3.Row]], dict[str, list[sqlite3.Row]]]:
    rows = conn.execute("""
      SELECT n.id,n.name,n.alias,n.protocol,n.country_code,n.country_label,
             n.country_override_code,n.country_override_label,
             p.reachable,p.latency_ms,p.created_at AS probe_at
      FROM csm_nodes n LEFT JOIN csm_probe_results p ON p.id=(
        SELECT p2.id FROM csm_probe_results p2 WHERE p2.node_id=n.id
        ORDER BY p2.created_at DESC,p2.rowid DESC LIMIT 1)
      ORDER BY n.rowid
    """).fetchall()
    output_names: dict[str, list[sqlite3.Row]] = {}
    originals: dict[str, list[sqlite3.Row]] = {}
    used: dict[str, int] = {}
    for row in rows:
        name = str(row["name"] or "")
        if name: originals.setdefault(name, []).append(row)
        alias = str(row["alias"] or "").strip()
        base = f"{alias}-{name}" if alias else name
        if not base:
            continue
        count = used.get(base, 0)
        used[base] = count + 1
        output_name = f"{base} ({count + 1})" if count else base
        output_names.setdefault(output_name, []).append(row)
    return output_names, originals


def _probe_for_output_name(name: str, aliases: dict[str, list[sqlite3.Row]], originals: dict[str, list[sqlite3.Row]]) -> sqlite3.Row | None:
    if name in aliases: return aliases[name][0]
    if name in originals: return originals[name][0]
    match = re.fullmatch(r"(.+) \((\d+)\)", name)
    if not match: return None
    base, position = match.group(1), int(match.group(2)) - 1
    values = originals.get(base) or []
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

    # A library Provider may be referenced by several strategy groups.  Public
    # detail reads always use the locally persisted snapshot and never fetch the
    # remote URL on the request path.
    provider_payloads: dict[str, tuple[list[str], str]] = {}

    def resolve_payload(provider_id: str, config: dict[str, Any]) -> tuple[list[str], str]:
        if provider_id in provider_payloads:
            return provider_payloads[provider_id]
        payload = _rule_provider_snapshot_payload(config)
        error = str(config.get("fetchError") or "")
        if not payload and not error:
            error = "规则快照尚未生成，等待定时拉取。"
        elif not payload:
            error = "规则快照为空，等待下次定时拉取。"
        value = (payload, error)
        provider_payloads[provider_id] = value
        return value

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
            payload, error = resolve_payload(provider_id, config)
            providers.append({
                "id": provider_id,
                "name": str(row["name"]),
                "kind": "规则订阅" if str(config.get("type") or "").lower() in {"cached", "http"} else "自定义",
                "behavior": str(config.get("behavior") or ""), "ruleCount": len(payload),
                "payload": payload, "error": error,
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



def _clean_test_domain(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    domain = (parsed.hostname or raw).strip().rstrip(".").lower()
    if (
        not domain
        or any(char.isspace() for char in domain)
        or "://" in raw
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is not None
    ):
        raise ToolboxError("INVALID_TEST_DOMAIN", "请只输入域名，不要包含协议、路径或端口。", status_code=422)
    return domain


def _domain_pattern_matches(domain: str, pattern: str) -> bool:
    value = str(pattern or "").strip().lower().lstrip("+")
    if not value:
        return False
    if value.startswith("."):
        value = value[1:]
    return domain == value or domain.endswith(f".{value}")


def _classical_provider_item_matches_domain(item: str, domain: str) -> bool:
    parts = [part.strip() for part in str(item or "").split(",")]
    if not parts:
        return False
    rule_type = parts[0].upper()
    if rule_type == "DOMAIN" and len(parts) >= 2:
        return domain == parts[1].lower()
    if rule_type == "DOMAIN-SUFFIX" and len(parts) >= 2:
        return _domain_pattern_matches(domain, parts[1])
    if rule_type == "DOMAIN-KEYWORD" and len(parts) >= 2:
        return parts[1].lower() in domain
    return False


def _rule_provider_matches_domain(config: dict[str, Any], domain: str) -> bool:
    behavior = str(config.get("behavior") or "domain").lower()
    payload = _rule_provider_snapshot_payload(config)
    if behavior == "domain":
        return any(_domain_pattern_matches(domain, item) for item in payload)
    if behavior == "classical":
        return any(_classical_provider_item_matches_domain(item, domain) for item in payload)
    return False


def _domain_rule_matches(rule: str, domain: str, providers: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    parts = [part.strip() for part in str(rule or "").split(",")]
    rule_type = parts[0].upper() if parts else ""
    target = _rule_target(rule)
    if rule_type == "MATCH":
        return True, target
    if rule_type == "DOMAIN" and len(parts) >= 2:
        return domain == parts[1].lower(), target
    if rule_type == "DOMAIN-SUFFIX" and len(parts) >= 2:
        return _domain_pattern_matches(domain, parts[1]), target
    if rule_type == "DOMAIN-KEYWORD" and len(parts) >= 2:
        return parts[1].lower() in domain, target
    if rule_type == "RULE-SET" and len(parts) >= 2:
        provider = providers.get(parts[1], {})
        return _rule_provider_matches_domain(provider, domain), target
    return False, target


def _test_domain_result(domain: str, rules: list[Any], providers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, str):
            continue
        matched, target = _domain_rule_matches(rule, domain, providers)
        if not matched:
            continue
        matches.append({"index": index, "rule": rule, "target": target})
        break
    effective = matches[0] if matches else None
    return {
        "domain": domain,
        "matched": effective is not None,
        "target": effective["target"] if effective else "",
        "rule": effective["rule"] if effective else "",
        "ruleIndex": effective["index"] if effective else None,
        "matches": matches,
    }

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
            content = _normalise_published_content(_decrypt(snapshot["content_encrypted"]))
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
                 "server": str(item.get("server") or ""), "port": item.get("port"),
                 "shareUri": proxy_share_uri(item)}
        probe = _probe_for_output_name(value["name"], aliases, originals)
        country_code = str(probe["country_override_code"] or probe["country_code"] or "") if probe else ""
        country_label = str(probe["country_override_label"] or "") if probe else ""
        if not country_label and probe and not probe["country_override_code"]:
            country_label = str(probe["country_label"] or "")
        if not country_label:
            country_label = COUNTRY_LABELS.get(country_code, country_code)
        value.update({"latencyMs": probe["latency_ms"] if probe else None,
                      "reachable": bool(probe["reachable"]) if probe else None,
                      "checkedAt": probe["probe_at"] if probe else None,
                      "country": country_code or None,
                      "countryLabel": country_label or None})
        proxies.append(value)
    return {"name": profile["name"], "publishedAt": snapshot["created_at"], "contentHash": hashlib.sha256(content.encode()).hexdigest(),
            "mode": str(document.get("mode") or "rule"), "proxies": proxies, "groups": groups,
            "ruleSetName": profile["rule_set_name"], "ruleSetUpdatedAt": profile["rule_set_updated_at"],
            "requestRuns": request_runs, "yaml": content}



def test_rule_set_domain(rule_set_id: str, domain: str, user: User) -> dict[str, Any]:
    """Test a domain against a live rule set using only local Provider snapshots."""
    clean_domain = _clean_test_domain(domain)
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (rule_set_id,)).fetchone()
        if not row:
            _not_found("规则组")
        rule_set = _materialize_rule_provider_bindings(conn, _rule_row(row), inline_rule_providers=True)
    return _test_domain_result(clean_domain, rule_set["rules"], {})


def test_public_subscription_domain(token: str, domain: str) -> dict[str, Any] | None:
    """Test a domain against the published rules and local Provider snapshots."""
    clean_domain = _clean_test_domain(domain)
    if not token or len(token) > 300:
        return None
    with connection_context() as conn:
        index = conn.execute("SELECT user_id,profile_id FROM csm_public_tokens WHERE token_hash=? AND enabled=1", (_token_hash(token),)).fetchone()
    if not index:
        return None
    init_database(index["user_id"])
    with user_tool_connection_context(index["user_id"], TOOL_ID) as conn:
        profile = conn.execute("SELECT * FROM csm_profiles WHERE id=?", (index["profile_id"],)).fetchone()
        snapshot = conn.execute("SELECT * FROM csm_published_snapshots WHERE profile_id=? ORDER BY created_at DESC LIMIT 1", (index["profile_id"],)).fetchone()
        if not profile or not snapshot:
            return None
        document = yaml.safe_load(_decrypt(snapshot["content_encrypted"])) or {}
        rules = [str(item) for item in document.get("rules") or [] if isinstance(item, str)]
        providers: dict[str, dict[str, Any]] = {}
        rule_row = conn.execute("SELECT * FROM csm_rule_sets WHERE id=?", (profile["rule_set_id"],)).fetchone() if profile["rule_set_id"] else None
        if rule_row:
            meta = _loads(rule_row["import_meta_json"], {})
            bindings = meta.get("providerBindings") if isinstance(meta, dict) else {}
            provider_ids = {
                str(item)
                for values in bindings.values() if isinstance(values, list)
                for item in values if item
            }
            for provider_id in provider_ids:
                provider_row = conn.execute("SELECT * FROM csm_rule_providers WHERE id=?", (provider_id,)).fetchone()
                if provider_row:
                    providers[str(provider_row["provider_key"])] = _normalise_legacy_manual_provider(
                        _loads(provider_row["config_json"], {})
                    )
        for rule in rules:
            parts = [part.strip() for part in rule.split(",")]
            if parts[0].upper() != "RULE-SET" or len(parts) < 2:
                continue
            provider = providers.get(parts[1])
            if provider and not _rule_provider_snapshot_payload(provider):
                raise ToolboxError(
                    "RULE_PROVIDER_SNAPSHOT_NOT_READY",
                    "规则快照尚未生成，等待定时拉取后再试。",
                    status_code=422,
                )
    return _test_domain_result(clean_domain, rules, providers)

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
    now_iso = datetime.now(timezone.utc).isoformat()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        source_counts = conn.execute("SELECT COUNT(*) AS total,SUM(status='healthy') AS healthy,SUM(status='error') AS error FROM csm_sources").fetchone()
        node_count = int(conn.execute("SELECT COUNT(*) FROM csm_nodes").fetchone()[0])
        custom_nodes = int(conn.execute("SELECT COUNT(*) FROM csm_nodes WHERE is_custom=1").fetchone()[0])
        source_node_count = int(conn.execute("SELECT COUNT(*) FROM csm_node_sources").fetchall()[0][0])
        probe_latest = conn.execute("""WITH latest AS (
          SELECT node_id,reachable,latency_ms,ROW_NUMBER() OVER(PARTITION BY node_id ORDER BY created_at DESC) rn
          FROM csm_probe_results)
          SELECT COUNT(*) AS probed,SUM(reachable=1) AS reachable,AVG(CASE WHEN reachable=1 THEN latency_ms END) AS avg_latency
          FROM latest WHERE rn=1""").fetchone()
        published = int(conn.execute("SELECT COUNT(*) FROM csm_profiles WHERE published_at IS NOT NULL").fetchone()[0])
        draft_profiles = int(conn.execute("SELECT COUNT(*) FROM csm_profiles WHERE published_at IS NULL").fetchone()[0])
        degraded_profiles = int(conn.execute("SELECT COUNT(*) FROM csm_profiles WHERE published_status='degraded'").fetchone()[0])
        rule_sets = int(conn.execute("SELECT COUNT(*) FROM csm_rule_sets").fetchone()[0])
        node_groups = int(conn.execute("SELECT COUNT(*) FROM csm_node_groups").fetchone()[0])
        latency_groups = int(conn.execute("SELECT COUNT(*) FROM csm_node_groups WHERE kind='latency'").fetchone()[0])
        provider_rows = conn.execute("SELECT config_json FROM csm_rule_providers").fetchall()
        requests_24h = int(conn.execute("SELECT COUNT(*) FROM csm_subscription_requests WHERE requested_at>=?", (now_iso,)).fetchone()[0])
        protocol = [{"name": r["protocol"], "value": r["count"]} for r in conn.execute("SELECT protocol,COUNT(*) count FROM csm_nodes GROUP BY protocol ORDER BY count DESC")]
        regions = [{"name": r["region"], "value": r["count"]} for r in conn.execute("""SELECT COALESCE(NULLIF(country_override_label,''),NULLIF(country_override_code,''),NULLIF(country_label,''),NULLIF(country_code,''),'未识别') region,COUNT(*) count
          FROM csm_nodes GROUP BY region ORDER BY count DESC LIMIT 8""")]
        latencies = [int(r["latency_ms"]) for r in conn.execute("""WITH latest AS (
          SELECT latency_ms,ROW_NUMBER() OVER(PARTITION BY node_id ORDER BY created_at DESC) rn
          FROM csm_probe_results WHERE reachable=1)
          SELECT latency_ms FROM latest WHERE rn=1 ORDER BY latency_ms""")]
        sources = [_row_source(r) for r in conn.execute("SELECT * FROM csm_sources ORDER BY CASE status WHEN 'error' THEN 0 ELSE 1 END,last_attempt_at DESC LIMIT 12")]
        profiles = [_profile_row(r) for r in conn.execute(f"{_PROFILE_WITH_RULE_SET} ORDER BY p.updated_at DESC LIMIT 12")]

    provider_errors = 0
    for row in provider_rows:
        config = _loads(row["config_json"], {})
        if isinstance(config, dict) and config.get("fetchError"):
            provider_errors += 1
    sources_total = int(source_counts["total"] or 0)
    healthy_sources = int(source_counts["healthy"] or 0)
    error_sources = int(source_counts["error"] or 0)
    probed_nodes = int(probe_latest["probed"] or 0)
    reachable_nodes = int(probe_latest["reachable"] or 0)
    avg_latency = int(probe_latest["avg_latency"] or 0) if probe_latest["avg_latency"] is not None else None
    profile_errors = sum(1 for profile in profiles for item in profile["validation"] if item.get("level") == "error")
    alerts = []
    for source in sources:
        if source["status"] == "error": alerts.append({"kind": "source", "level": "error", "message": f"订阅源 {source['name']} 刷新失败：{source['lastError']}"})
    if provider_errors:
        alerts.append({"kind": "ruleProvider", "level": "error", "message": f"{provider_errors} 个 Rule Provider 最近拉取失败，已沿用上次成功内容"})
    for profile in profiles:
        for item in profile["validation"]:
            if item.get("level") == "error": alerts.append({"kind": "profile", "level": "error", "profileId": profile["id"], "message": item.get("message", "配置校验异常")})

    def rate(part: int, total: int) -> int:
        return round(part * 100 / total) if total else 0

    latency_p50 = latencies[len(latencies) // 2] if latencies else None
    latency_p95 = latencies[min(len(latencies) - 1, math.ceil(len(latencies) * .95) - 1)] if latencies else None
    metrics = {
        "sources": sources_total, "healthySources": healthy_sources, "errorSources": error_sources,
        "nodes": node_count, "customNodes": custom_nodes, "nodesBeforeDedupe": source_node_count,
        "nodesDeduplicated": max(0, source_node_count - node_count), "probedNodes": probed_nodes,
        "tcpReachable": reachable_nodes, "avgLatencyMs": avg_latency, "latencyP50Ms": latency_p50, "latencyP95Ms": latency_p95,
        "profiles": published + draft_profiles, "publishedProfiles": published, "draftProfiles": draft_profiles,
        "degradedProfiles": degraded_profiles, "profileErrors": profile_errors, "ruleSets": rule_sets,
        "ruleProviders": len(provider_rows), "providerErrors": provider_errors, "nodeGroups": node_groups,
        "latencyNodeGroups": latency_groups, "requests24h": requests_24h, "alerts": len(alerts),
    }
    health = {
        "sourceHealthRate": rate(healthy_sources, sources_total),
        "nodeReachabilityRate": rate(reachable_nodes, probed_nodes),
        "profileHealthRate": rate(published - degraded_profiles, published + draft_profiles),
        "dedupeSavedRate": rate(max(0, source_node_count - node_count), source_node_count),
    }
    return {"metrics": metrics, "health": health, "protocolDistribution": protocol, "regionDistribution": regions, "sources": sources, "profiles": profiles, "alerts": alerts[:30]}

def refresh_runs(user: User, limit: int = 100) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute("SELECT r.*,s.name AS source_name FROM csm_refresh_runs r JOIN csm_sources s ON s.id=r.source_id ORDER BY r.created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [{"id": row["id"], "sourceId": row["source_id"], "sourceName": row["source_name"], "status": row["status"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "durationMs": row["duration_ms"], "nodesBefore": row["nodes_before"], "nodesAfter": row["nodes_after"], "error": row["error"]} for row in rows]


def profile_refresh_runs(user: User, profile_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_database(user.id)
    params: list[Any] = []
    where = ""
    if profile_id:
        where = "WHERE r.profile_id=?"
        params.append(profile_id)
    params.append(max(1, min(limit, 500)))
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute(f"""SELECT r.*,p.name AS profile_name FROM csm_profile_refresh_runs r
          JOIN csm_profiles p ON p.id=r.profile_id {where} ORDER BY r.created_at DESC LIMIT ?""", params).fetchall()
    return [{"id": row["id"], "profileId": row["profile_id"], "profileName": row["profile_name"], "status": row["status"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "durationMs": row["duration_ms"], "error": row["error"]} for row in rows]


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
        _set_auto_update_last_run(user_id, "sourceRefreshSeconds", now)
        for source_id in due:
            try: refresh_source(source_id, ScheduledUser(), auto_rebuild=True)  # type: ignore[arg-type]
            except ToolboxError: pass


def refresh_due_profiles() -> None:
    """Refresh published aggregate-link snapshots on their own schedule."""
    now = _now()
    for user_id, _path in list_user_tool_dbs(TOOL_ID):
        init_database(user_id)
        class ScheduledUser:  # noqa: D101
            id = user_id
        _set_auto_update_last_run(user_id, "profileRefreshSeconds", now)
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM csm_profiles WHERE published_at IS NOT NULL AND (next_refresh_at IS NULL OR next_refresh_at<=?)", (now,)
            )]
        for profile_id in ids:
            try:
                publish_profile(profile_id, ScheduledUser())  # type: ignore[arg-type]
            except ToolboxError:
                with user_tool_connection_context(user_id, TOOL_ID) as conn:
                    conn.execute("UPDATE csm_profiles SET published_status='degraded',next_refresh_at=? WHERE id=?", (datetime.fromtimestamp(time.time() + DEFAULT_PROFILE_REFRESH_SECONDS, timezone.utc).isoformat(), profile_id))


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


def _refresh_rule_providers_for_user(user_id: str, *, only_due: bool) -> bool:
    """Refresh rule providers for one user; returns whether payloads changed."""
    now = datetime.now(timezone.utc)
    init_database(user_id)
    _set_auto_update_last_run(user_id, "ruleProviderRefreshSeconds", now.isoformat())
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        rows = conn.execute("SELECT id,config_json FROM csm_rule_providers").fetchall()
    changed = False
    for row in rows:
        config = _loads(row["config_json"], {})
        if not isinstance(config, dict) or (only_due and not _rule_provider_refresh_due(config, now)):
            continue
        url = _rule_provider_source_url(config)
        if not url:
            continue
        try:
            payload = _download_rule_provider_payload(url)
            next_config = {**config, "payload": payload, "fetchedAt": _now(), "fetchError": ""}
            changed = changed or payload != _rule_provider_snapshot_payload(config)
        except ToolboxError as exc:
            # Keep the last successful payload and record the failure; the
            # provider interval acts as the retry cooldown.
            next_config = {**config, "fetchedAt": _now(), "fetchError": str(exc.message)}
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            conn.execute("UPDATE csm_rule_providers SET config_json=? WHERE id=?",
                         (_json(next_config), row["id"]))
    if changed:
        class ScheduledUser:  # noqa: D101
            id = user_id
        _rebuild_changed_published_profiles(ScheduledUser())  # type: ignore[arg-type]
    return changed


def refresh_due_rule_providers() -> None:
    """Scheduler entry: keep URL Provider snapshots fresh without request-time I/O."""
    for user_id, _path in list_user_tool_dbs(TOOL_ID):
        _refresh_rule_providers_for_user(user_id, only_due=True)


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
        _start_geoip_refresh(ScheduledUser())  # type: ignore[arg-type]
        if has_latency_group:
            _rebuild_changed_published_profiles(ScheduledUser())  # type: ignore[arg-type]


def probe_due_nodes() -> None:
    """Run the node probe according to the per-user unified interval."""
    now = datetime.now(timezone.utc)
    for user_id, _path in list_user_tool_dbs(TOOL_ID):
        init_database(user_id)
        settings = auto_update_settings(type("ScheduledUser", (), {"id": user_id})())
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            row = conn.execute("SELECT value FROM csm_settings WHERE key='auto_update_last_node_probe_run_at'").fetchone()
            last = _parse_utc(row["value"]) if row else None
            if last and now < last + timedelta(seconds=settings["nodeProbeSeconds"]):
                continue
        _set_auto_update_last_run(user_id, "nodeProbeSeconds", now.isoformat())
        probe_all_nodes_for_user(user_id)


def probe_all_nodes_for_user(user_id: str) -> None:
    class ScheduledUser:  # noqa: D101
        id = user_id
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        node_ids = [row["id"] for row in conn.execute("SELECT id FROM csm_nodes")]
        has_latency_group = bool(conn.execute("SELECT 1 FROM csm_node_groups WHERE kind='latency' LIMIT 1").fetchone())
    for offset in range(0, len(node_ids), 1000):
        probe_nodes(node_ids[offset:offset + 1000], ScheduledUser())  # type: ignore[arg-type]
    _start_geoip_refresh(ScheduledUser())  # type: ignore[arg-type]
    if has_latency_group:
        _rebuild_changed_published_profiles(ScheduledUser())  # type: ignore[arg-type]


def run_auto_update_task(task: str, user: User) -> dict[str, Any]:
    """Manually run one scheduled auto-update task immediately for the caller."""
    init_database(user.id)
    task_setting_keys = {"sources": "sourceRefreshSeconds", "ruleProviders": "ruleProviderRefreshSeconds", "probe": "nodeProbeSeconds", "profiles": "profileRefreshSeconds"}
    if task not in task_setting_keys:
        raise ToolboxError("CSM_BAD_REQUEST", "未知的自动更新任务。", status_code=400)
    started = _now()
    _set_auto_update_last_run(user.id, task_setting_keys[task], started)
    last_run_at = auto_update_settings(user)["lastRunAt"]
    if task == "sources":
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            source_ids = [row["id"] for row in conn.execute("SELECT id FROM csm_sources WHERE enabled=1")]
        ok = failed = 0
        for source_id in source_ids:
            try:
                refresh_source(source_id, user, auto_rebuild=False)
                ok += 1
            except ToolboxError:
                failed += 1
        if ok:
            _rebuild_published_profiles(user)
        message = f"已刷新 {ok} 个订阅源" + (f"，{failed} 个失败" if failed else "")
        return {"task": task, "message": message, "lastRunAt": last_run_at}
    if task == "ruleProviders":
        changed = _refresh_rule_providers_for_user(user.id, only_due=False)
        return {"task": task, "message": "Rule Provider 已刷新" if changed else "Rule Provider 已检查，内容无变化", "lastRunAt": last_run_at}
    if task == "probe":
        probe_all_nodes_for_user(user.id)
        return {"task": task, "message": "已探测全部节点", "lastRunAt": last_run_at}
    if task == "profiles":
        published = [profile for profile in list_profiles(user) if profile["publishedAt"]]
        ok = 0
        for profile in published:
            try:
                publish_profile(profile["id"], user)
                ok += 1
            except ToolboxError:
                with user_tool_connection_context(user.id, TOOL_ID) as conn:
                    conn.execute("UPDATE csm_profiles SET published_status='degraded',updated_at=? WHERE id=?", (_now(), profile["id"]))
        return {"task": task, "message": f"已重新生成 {ok}/{len(published)} 个聚合订阅链接", "lastRunAt": last_run_at}
    raise ToolboxError("CSM_BAD_REQUEST", "未知的自动更新任务。", status_code=400)
