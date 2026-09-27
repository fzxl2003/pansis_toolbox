"""Administrator-managed outbound proxy configuration and domain routing."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database

_CONFIG_KEY = "global_outbound_proxy"
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")

# Tools can extend this catalogue by adding their outbound domains here.  The
# settings API presents every entry to the administrator before it is enabled.
SUGGESTED_DOMAINS = (
    {"domain": "github.com", "toolId": "git_blog", "toolName": "Git 博客", "description": "GitHub HTTPS 仓库访问"},
    {"domain": "ssh.github.com", "toolId": "git_blog", "toolName": "Git 博客", "description": "GitHub Deploy Key SSH 拉取"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if not _DOMAIN_RE.fullmatch(domain):
        raise ToolboxError("INVALID_PROXY_DOMAIN", "代理域名格式不合法", status_code=400)
    return domain


def _default() -> dict[str, Any]:
    return {"protocol": "http", "host": "", "port": 7890, "selectedDomains": [], "customDomains": []}


def _load() -> dict[str, Any]:
    init_database()
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM platform_settings WHERE key=?", (_CONFIG_KEY,)).fetchone()
    if not row:
        return _default()
    try:
        data = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return _default()
    base = _default()
    if isinstance(data, dict):
        base.update({key: data[key] for key in base if key in data})
    return base


def get_settings() -> dict[str, Any]:
    config = _load()
    selected = sorted({_normalise_domain(str(item)) for item in config["selectedDomains"] if isinstance(item, str) and _DOMAIN_RE.fullmatch(item.rstrip("."))})
    custom = sorted({_normalise_domain(str(item)) for item in config["customDomains"] if isinstance(item, str) and _DOMAIN_RE.fullmatch(item.rstrip("."))})
    return {
        "protocol": config["protocol"] if config["protocol"] in {"http", "https", "socks5", "socks5h"} else "http",
        "host": str(config["host"]),
        "port": int(config["port"]),
        "selectedDomains": selected,
        "customDomains": custom,
        "suggestedDomains": SUGGESTED_DOMAINS,
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    protocol = str(payload.get("protocol") or "http").lower()
    host = str(payload.get("host") or "").strip()
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError) as exc:
        raise ToolboxError("INVALID_PROXY_PORT", "代理端口必须是 1 到 65535 的整数", status_code=400) from exc
    if protocol not in {"http", "https", "socks5", "socks5h"}:
        raise ToolboxError("INVALID_PROXY_PROTOCOL", "代理协议不受支持", status_code=400)
    if not host or not _HOST_RE.fullmatch(host) or ".." in host:
        raise ToolboxError("INVALID_PROXY_HOST", "代理地址格式不合法", status_code=400)
    if not 1 <= port <= 65535:
        raise ToolboxError("INVALID_PROXY_PORT", "代理端口必须是 1 到 65535 的整数", status_code=400)
    raw_domains = payload.get("selectedDomains", [])
    raw_custom_domains = payload.get("customDomains", [])
    if not isinstance(raw_domains, list) or not isinstance(raw_custom_domains, list):
        raise ToolboxError("INVALID_PROXY_DOMAINS", "代理域名必须是列表", status_code=400)
    domains = sorted({_normalise_domain(str(item)) for item in raw_domains})
    custom_domains = sorted({_normalise_domain(str(item)) for item in raw_custom_domains})
    value = json.dumps({"protocol": protocol, "host": host, "port": port, "selectedDomains": domains, "customDomains": custom_domains}, ensure_ascii=False)
    init_database()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO platform_settings(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (_CONFIG_KEY, value, _now()),
        )
    return get_settings()


def get_proxy_url_for_host(host: str) -> str:
    """Return the global proxy only when the target is enabled by the admin."""
    config = get_settings()
    hostname = host.strip().lower().rstrip(".")
    if not config["host"] or not any(hostname == domain or hostname.endswith(f".{domain}") for domain in config["selectedDomains"]):
        return ""
    return f"{config['protocol']}://{config['host']}:{config['port']}"
