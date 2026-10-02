from __future__ import annotations

from datetime import datetime, timezone
import base64
import html
from io import BytesIO
from email.utils import format_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
import qrcode
import qrcode.image.svg

from backend.app.core.security import require_user
from tools.airplay_subscription_manager.backend import service

service.migrate_tool_identity()

router = APIRouter()


class SourcePayload(BaseModel):
    name: str = ""
    url: str
    userAgent: str = ""
    refreshSeconds: int = Field(default=21600, ge=60, le=604800)
    enabled: bool = True


class SourcePatch(BaseModel):
    name: str | None = None
    url: str | None = None
    userAgent: str | None = None
    refreshSeconds: int | None = Field(default=None, ge=60, le=604800)
    enabled: bool | None = None


class ProfilePayload(BaseModel):
    name: str = "未命名聚合"
    settings: dict[str, Any] = {}
    ruleSetId: str | None = None


class ProfilePatch(BaseModel):
    name: str | None = None
    settings: dict[str, Any] | None = None
    ruleSetId: str | None = None


class AutoUpdateSettingsPayload(BaseModel):
    sourceRefreshSeconds: int = Field(ge=60, le=604800)
    ruleProviderRefreshSeconds: int = Field(ge=60, le=604800)
    nodeProbeSeconds: int = Field(ge=60, le=604800)
    profileRefreshSeconds: int = Field(ge=60, le=604800)


class RuleSetPayload(BaseModel):
    name: str = "规则库"
    rules: list[Any] = Field(default_factory=list)
    providers: dict[str, Any] = Field(default_factory=dict)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    importMeta: dict[str, Any] = Field(default_factory=dict)


class RuleImportPayload(BaseModel):
    name: str = "导入规则"
    content: str = ""
    url: str = ""


class ProbePayload(BaseModel):
    nodeIds: list[str] = Field(default_factory=list, max_length=1000)


class NodeGeoIpPayload(BaseModel):
    nodeIds: list[str] = Field(default_factory=list, max_length=1000)


class NodeAliasPayload(BaseModel):
    alias: str = Field(default="", max_length=120)


class NodeCountryPayload(BaseModel):
    countryCode: str = Field(default="", max_length=2)
    countryLabel: str = Field(default="", max_length=80)


class CustomNodePayload(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    alias: str | None = Field(default=None, max_length=120)


class RuleProviderLibraryPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    providerKey: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)


class RuleProviderPackagePayload(BaseModel):
    providerIds: list[str] = Field(default_factory=list, max_length=100)
    target: str = Field(min_length=1, max_length=120)


class RuleProviderCopyPayload(BaseModel):
    mode: str = Field(default="original", pattern="^(original|manual)$")


class NodeGroupPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(custom|region|latency)$")
    config: dict[str, Any] = Field(default_factory=dict)


class DomainTestPayload(BaseModel):
    domain: str = Field(min_length=1, max_length=300)


@router.get("/dashboard")
def get_dashboard(request: Request) -> dict[str, Any]:
    return service.dashboard(require_user(request))


@router.get("/auto-update-settings")
def get_auto_update_settings(request: Request) -> dict[str, Any]:
    return service.auto_update_settings(require_user(request))


@router.put("/auto-update-settings")
def put_auto_update_settings(request: Request, payload: AutoUpdateSettingsPayload) -> dict[str, Any]:
    return service.save_auto_update_settings(payload.model_dump(), require_user(request))

@router.post("/auto-update/run/{task}")
def post_auto_update_run(request: Request, task: str) -> dict[str, Any]:
    return service.run_auto_update_task(task, require_user(request))


@router.get("/sources")
def get_sources(request: Request) -> dict[str, Any]:
    return {"sources": service.list_sources(require_user(request))}


@router.post("/sources")
def post_source(request: Request, payload: SourcePayload) -> dict[str, Any]:
    return {"source": service.create_source(payload.model_dump(), require_user(request))}


@router.put("/sources/{source_id}")
def put_source(request: Request, source_id: str, payload: SourcePatch) -> dict[str, Any]:
    return {"source": service.update_source(source_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/sources/{source_id}")
def remove_source(request: Request, source_id: str) -> dict[str, bool]:
    service.delete_source(source_id, require_user(request)); return {"deleted": True}


@router.post("/sources/{source_id}/refresh")
def refresh_source(request: Request, source_id: str) -> dict[str, Any]:
    return service.refresh_source(source_id, require_user(request))


@router.get("/nodes")
def get_nodes(request: Request, protocol: str = "", sourceId: str = "", search: str = "") -> dict[str, Any]:
    return {"nodes": service.list_nodes(require_user(request), {"protocol": protocol, "sourceId": sourceId, "search": search})}


@router.post("/nodes")
def post_custom_node(request: Request, payload: CustomNodePayload) -> dict[str, Any]:
    return {"node": service.create_custom_node(payload.content, require_user(request), payload.alias or "")}


@router.post("/nodes/probe")
def probe_nodes(request: Request, payload: ProbePayload) -> dict[str, Any]:
    return {"results": service.probe_nodes(payload.nodeIds, require_user(request)), "notice": "TCP 可达不代表代理可用或真实延迟。"}


@router.post("/nodes/geoip")
def refresh_node_geoip(request: Request, payload: NodeGeoIpPayload) -> dict[str, Any]:
    return {"nodes": service.refresh_node_geoip(payload.nodeIds, require_user(request))}


@router.post("/nodes/{node_id}/copy")
def copy_node(request: Request, node_id: str) -> dict[str, Any]:
    return {"node": service.copy_subscription_node(node_id, require_user(request))}


@router.put("/nodes/{node_id}")
def put_custom_node(request: Request, node_id: str, payload: CustomNodePayload) -> dict[str, Any]:
    return {"node": service.update_custom_node(node_id, payload.content, require_user(request), payload.alias)}


@router.delete("/nodes/{node_id}")
def remove_custom_node(request: Request, node_id: str) -> dict[str, bool]:
    service.delete_custom_node(node_id, require_user(request))
    return {"deleted": True}


@router.put("/nodes/{node_id}/alias")
def put_node_alias(request: Request, node_id: str, payload: NodeAliasPayload) -> dict[str, Any]:
    return {"node": service.update_node_alias(node_id, payload.alias, require_user(request))}


@router.put("/nodes/{node_id}/country")
def put_node_country(request: Request, node_id: str, payload: NodeCountryPayload) -> dict[str, Any]:
    return {"node": service.update_node_country_override(
        node_id, payload.countryCode, payload.countryLabel, require_user(request)
    )}


@router.get("/node-groups")
def get_node_groups(request: Request) -> dict[str, Any]:
    return {"groups": service.list_node_groups(require_user(request))}


@router.post("/node-groups")
def post_node_group(request: Request, payload: NodeGroupPayload) -> dict[str, Any]:
    return {"group": service.save_node_group(payload.model_dump(), require_user(request))}


@router.put("/node-groups/{group_id}")
def put_node_group(request: Request, group_id: str, payload: NodeGroupPayload) -> dict[str, Any]:
    return {"group": service.save_node_group(payload.model_dump(), require_user(request), group_id)}


@router.delete("/node-groups/{group_id}")
def remove_node_group(request: Request, group_id: str) -> dict[str, bool]:
    service.delete_node_group(group_id, require_user(request)); return {"deleted": True}


@router.get("/profiles")
def get_profiles(request: Request) -> dict[str, Any]:
    return {"profiles": service.list_profiles(require_user(request))}


@router.post("/profiles")
def post_profile(request: Request, payload: ProfilePayload) -> dict[str, Any]:
    return {"profile": service.create_profile(payload.model_dump(), require_user(request))}


@router.put("/profiles/{profile_id}")
def put_profile(request: Request, profile_id: str, payload: ProfilePatch) -> dict[str, Any]:
    return {"profile": service.update_profile(profile_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/profiles/{profile_id}")
def remove_profile(request: Request, profile_id: str) -> dict[str, bool]:
    service.delete_profile(profile_id, require_user(request)); return {"deleted": True}


@router.post("/profiles/{profile_id}/validate")
def validate_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.validate_profile(profile_id, require_user(request))


@router.get("/profiles/{profile_id}/preview")
def preview_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.preview_profile(profile_id, require_user(request))


@router.post("/profiles/{profile_id}/publish")
def publish_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.publish_profile(profile_id, require_user(request))


@router.post("/profiles/{profile_id}/refresh")
def refresh_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.publish_profile(profile_id, require_user(request))


@router.get("/profile-refresh-runs")
def get_profile_refresh_runs(request: Request, profileId: str | None = None, limit: int = 100) -> dict[str, Any]:
    return {"runs": service.profile_refresh_runs(require_user(request), profileId, limit)}


@router.get("/rule-providers")
def get_rule_providers(request: Request) -> dict[str, Any]:
    return {"providers": service.list_rule_providers(require_user(request))}


@router.post("/rule-providers")
def post_rule_provider(request: Request, payload: RuleProviderLibraryPayload) -> dict[str, Any]:
    return {"provider": service.save_rule_provider(payload.model_dump(), require_user(request))}


@router.put("/rule-providers/{provider_id}")
def put_rule_provider(request: Request, provider_id: str, payload: RuleProviderLibraryPayload) -> dict[str, Any]:
    return {"provider": service.save_rule_provider(payload.model_dump(), require_user(request), provider_id)}


@router.delete("/rule-providers/{provider_id}")
def remove_rule_provider(request: Request, provider_id: str) -> dict[str, bool]:
    service.delete_rule_provider(provider_id, require_user(request)); return {"deleted": True}


@router.post("/rule-providers/{provider_id}/copy")
def copy_rule_provider(request: Request, provider_id: str, payload: RuleProviderCopyPayload) -> dict[str, Any]:
    return {"provider": service.copy_rule_provider(provider_id, payload.mode, require_user(request))}


@router.post("/rule-providers/package")
def package_rule_providers(request: Request, payload: RuleProviderPackagePayload) -> dict[str, Any]:
    return service.package_rule_providers(payload.providerIds, payload.target, require_user(request))


@router.get("/rule-sets")
def get_rule_sets(request: Request) -> dict[str, Any]:
    return {"ruleSets": service.list_rule_sets(require_user(request))}


@router.post("/rule-sets")
def post_rule_set(request: Request, payload: RuleSetPayload) -> dict[str, Any]:
    return {"ruleSet": service.save_rule_set(payload.model_dump(), require_user(request))}


@router.put("/rule-sets/{rule_set_id}")
def put_rule_set(request: Request, rule_set_id: str, payload: RuleSetPayload) -> dict[str, Any]:
    return {"ruleSet": service.save_rule_set(payload.model_dump(), require_user(request), rule_set_id)}


@router.delete("/rule-sets/{rule_set_id}")
def remove_rule_set(request: Request, rule_set_id: str) -> dict[str, bool]:
    service.delete_rule_set(rule_set_id, require_user(request)); return {"deleted": True}


@router.post("/rule-sets/import")
def import_rule_set(request: Request, payload: RuleImportPayload) -> dict[str, Any]:
    return {"ruleSet": service.import_rule_set(payload.model_dump(), require_user(request))}


@router.post("/rule-sets/{rule_set_id}/test-domain")
def test_rule_set_domain(request: Request, rule_set_id: str, payload: DomainTestPayload) -> dict[str, Any]:
    return service.test_rule_set_domain(rule_set_id, payload.domain, require_user(request))


@router.get("/refresh-runs")
def get_refresh_runs(request: Request, limit: int = 100) -> dict[str, Any]:
    return {"runs": service.refresh_runs(require_user(request), limit)}


def _subscription_qr_data_url(download_url: str) -> str:
    """Encode the public YAML download URL as a self-contained SVG QR code."""
    image = qrcode.make(download_url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buffer = BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _forwarded_parameter(request: Request, name: str) -> str:
    forwarded = request.headers.get("forwarded", "").split(",", 1)[0]
    for part in forwarded.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == name:
            return value.strip().strip('"')
    return ""


def _external_base_url(request: Request) -> str:
    """Build the browser-facing origin while honoring common reverse-proxy headers."""
    parts = urlsplit(str(request.base_url))
    scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = scheme or _forwarded_parameter(request, "proto").lower()
    if scheme not in {"http", "https"}:
        scheme = parts.scheme
    host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    host = host or _forwarded_parameter(request, "host") or parts.netloc
    if not host or any(character in host for character in "/\\\r\n\t "):
        host = parts.netloc
    return urlunsplit((scheme, host, parts.path.rstrip("/"), "", ""))


def _subscription_details_html(
    token: str,
    details: dict[str, Any],
    download_url: str | None = None,
) -> str:
    e = html.escape
    download_url = download_url or f"/sub/airplay/{token}"
    qr_data_url = _subscription_qr_data_url(download_url)
    proxies = details.get("proxies") or []
    groups = details.get("groups") or []
    runs = details.get("requestRuns") or []
    max_speed_dots = 24

    def latency_level(item: dict[str, Any]) -> str:
        if not item.get("checkedAt"): return "unknown"
        if item.get("reachable") is False: return "bad"
        value = item.get("latencyMs")
        if value is None: return "unknown"
        return "good" if float(value) <= 200 else "warn" if float(value) <= 500 else "bad"

    def latency(item: dict[str, Any]) -> str:
        if not item.get("checkedAt"): return '<span class="latency muted">未探测</span>'
        if item.get("reachable") is False: return '<span class="latency bad">不可达</span>'
        value = item.get("latencyMs")
        if value is None: return '<span class="latency warn">无延迟</span>'
        return f'<span class="latency {latency_level(item)}">{float(value):.0f} ms</span>'

    def speed_dot(item: dict[str, Any]) -> str:
        value = item.get("latencyMs")
        if not item.get("checkedAt"): label = "未探测"
        elif item.get("reachable") is False: label = "不可达"
        elif value is None: label = "无延迟"
        else: label = f"{float(value):.0f} ms"
        title = f"{item.get('name') or '节点'} · {label}"
        return f'<i class="speed-dot {latency_level(item)}" title="{e(title)}"></i>'

    no_payload = '<li class="muted">暂无内容</li>'
    no_members = '<span class="muted">暂无成员</span>'
    no_providers = '<p class="muted">该策略组未绑定规则内容。</p>'

    def request_status(code: Any) -> str:
        return '<span class="status ok">成功</span>' if int(code or 200) == 200 else f'<span class="status bad">{int(code or 0)}</span>'

    def relative_time(value: Any) -> str:
        text = str(value or "").strip()
        if not text: return "—"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = int((datetime.now(timezone.utc) - parsed).total_seconds())
        if seconds < 60: return "刚刚"
        minutes = seconds // 60
        if minutes < 60: return f"{minutes} 分钟前"
        hours = minutes // 60
        if hours < 24: return f"{hours} 小时前"
        days = hours // 24
        if days < 30: return f"{days} 天前"
        months = days // 30
        if months < 12: return f"{months} 个月前"
        return f"{months // 12} 年前"

    node_qr_dialogs: list[str] = []
    node_rows_list: list[str] = []
    for index, item in enumerate(proxies):
        share_uri = str(item.get("shareUri") or "")
        country_code = str(item.get("country") or "").upper()
        country_label = str(item.get("countryLabel") or country_code or "未识别")
        actions = '<span class="muted">不支持</span>'
        if share_uri:
            modal_id = f"node-qr-modal-{index}"
            node_qr_dialogs.append(
                f'<dialog class="modal node-qr-modal" id="{modal_id}" aria-label="节点二维码"><article class="modal-panel">'
                f'<header class="modal-head"><div><strong>{e(str(item.get("name") or "节点"))}</strong><span>扫描二维码导入此节点</span></div>'
                f'<button type="button" class="modal-close" onclick="this.closest(\'dialog\').close()">关闭</button></header>'
                f'<div class="modal-body"><img class="node-qr-image" src="{_subscription_qr_data_url(share_uri)}" alt="{e(str(item.get("name") or "节点"))} 导入链接二维码"></div>'
                f'</article></dialog>'
            )
            actions = (
                f'<div class="node-actions"><button type="button" class="node-action" data-copy-node-link '
                f'data-node-link="{e(share_uri, quote=True)}" title="复制节点导入链接" aria-label="复制节点导入链接"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="10" height="11" rx="1"></rect><path d="M15 9V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h4"></path></svg></button>'
                f'<button type="button" class="node-action" title="显示节点二维码" aria-label="显示节点二维码" '
                f'onclick="document.getElementById(\'{modal_id}\').showModal()"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2v4h-2zM14 18h4v2h-4z"></path></svg></button></div>'
            )
        node_rows_list.append(
            f'<tr><td class="node-name">{e(str(item.get("name") or ""))}</td>'
            f'<td>{e(str(item.get("type") or ""))}</td>'
            f'<td class="mono">{e(str(item.get("server") or ""))}</td>'
            f'<td>{e(str(item.get("port") or ""))}</td>'
            f'<td data-country-code="{e(country_code, quote=True)}">{e(country_label)}</td>'
            f'<td>{latency(item)}</td><td>{actions}</td></tr>'
        )
    node_rows = "".join(node_rows_list)

    speed_dots = "".join(speed_dot(item) for item in proxies[:max_speed_dots])
    if len(proxies) > max_speed_dots:
        speed_dots += f'<span class="speed-more" title="共 {len(proxies)} 个节点">+{len(proxies) - max_speed_dots}</span>'
    if not speed_dots:
        speed_dots = '<i class="speed-dot unknown" title="暂无节点"></i>'

    group_cards: list[str] = []
    group_modals: list[str] = []
    provider_modals: list[str] = []
    provider_modal_keys: set[str] = set()
    provider_preview_limit = 5
    for index, group in enumerate(groups):
        members = [str(item) for item in group.get("proxies") or []]
        member_chips = "".join(f'<span class="chip">{e(member)}</span>' for member in members[:80])
        if len(members) > 80:
            member_chips += f'<span class="chip">另有 {len(members) - 80} 个成员</span>'
        provider_items: list[str] = []
        for provider_index, provider in enumerate(group.get("providers") or []):
            payload = [str(item) for item in provider.get("payload") or []]
            error = str(provider.get("error") or "")
            preview = payload[:provider_preview_limit]
            payload_html = "".join(f"<li><code>{e(item)}</code></li>" for item in preview)
            if error:
                payload_html = f'<li class="error-text">{e(error)}</li>' + payload_html
            provider_name = e(str(provider.get("name") or ""))
            provider_meta = e(
                f'{provider.get("kind") or ""} · {provider.get("behavior") or ""} · {provider.get("ruleCount") or 0} 条'
            )
            more_button = ""
            if len(payload) > provider_preview_limit:
                provider_id = str(provider.get("id") or f"{index}-{provider_index}")
                provider_modal_id = f"provider-modal-{provider_id}"
                if provider_id not in provider_modal_keys:
                    provider_modal_keys.add(provider_id)
                    provider_modals.append(
                        f'<dialog class="modal" id="{provider_modal_id}" aria-label="规则内容详情"><article class="modal-panel">'
                        f'<header class="modal-head"><div><strong>{provider_name}</strong><span>{provider_meta} · 全部 {len(payload)} 条</span></div>'
                        f'<button type="button" class="modal-close" onclick="this.closest(\'dialog\').close()">关闭</button></header>'
                        f'<div class="modal-body"><section><h3>规则内容</h3><ul class="payload payload-full">'
                        + "".join(f"<li><code>{e(item)}</code></li>" for item in payload)
                        + '</ul></section></div></article></dialog>'
                    )
                more_button = (
                    f'<button type="button" class="provider-more" onclick="this.closest(\'dialog\').close();'
                    f'document.getElementById(\'{provider_modal_id}\').showModal()">显示全部（{len(payload)} 条）</button>'
                )
            provider_items.append(
                f'<article class="provider-card"><header><strong>{provider_name}</strong>'
                f'<span>{provider_meta}</span></header>'
                f'<ul class="payload">{payload_html or no_payload}</ul>{more_button}</article>'
            )
        group_name = e(str(group.get("name") or ""))
        group_meta = e(f'{group.get("type") or ""} · {len(members)} 个成员 · {len(group.get("providers") or [])} 组规则内容')
        modal_id = f"group-modal-{index}"
        group_cards.append(
            f'<button type="button" class="group-card" onclick="document.getElementById(\'{modal_id}\').showModal()">'
            f'<span class="group-name">{group_name}</span><span class="group-meta">{group_meta}</span></button>'
        )
        group_modals.append(
            f'<dialog class="modal" id="{modal_id}" aria-label="策略组详情"><article class="modal-panel">'
            f'<header class="modal-head"><div><strong>{group_name}</strong><span>{group_meta}</span></div>'
            f'<button type="button" class="modal-close" onclick="this.closest(\'dialog\').close()">关闭</button></header>'
            f'<div class="modal-body"><section><h3>成员</h3><div class="chips">{member_chips or no_members}</div></section>'
            f'<section><h3>规则内容</h3><div class="providers">{"".join(provider_items) or no_providers}</div></section></div>'
            f'</article></dialog>'
        )

    log_rows = "".join(
        f'<tr><td>{e(str(item.get("requestedAt") or ""))}</td>'
        f'<td class="mono">{e(str(item.get("clientIp") or "—"))}</td>'
        f'<td class="user-agent">{e(str(item.get("userAgent") or "—"))}</td>'
        f'<td>{request_status(item.get("statusCode"))}</td></tr>'
        for item in runs
    )
    latest_request_raw = str(runs[0].get("requestedAt") or "") if runs else ""
    latest_request = relative_time(latest_request_raw)

    css = '''
:root{color-scheme:light dark;font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;--line:#e5e7eb;--muted:#64748b;--panel:#ffffff;--bg:#f5f7fb;--text:#111827}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}
main{max-width:1180px;margin:0 auto;padding:34px 20px 70px}
.hero{position:relative;overflow:hidden;border-radius:24px;padding:34px;color:#fff;background:linear-gradient(135deg,#172554,#2563eb 62%,#0ea5e9);box-shadow:0 20px 45px rgba(37,99,235,.18)}
.hero:after{content:"";position:absolute;right:-70px;top:-90px;width:280px;height:280px;border-radius:999px;background:rgba(255,255,255,.12)}
.hero-inner{position:relative;z-index:1;display:flex;justify-content:space-between;gap:24px;align-items:flex-start;flex-wrap:wrap}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.78}h1{margin:8px 0 12px;font-size:clamp(24px,4vw,35px);line-height:1.15}
.meta{margin:0;font-size:13px;line-height:1.8;opacity:.86}.meta b{font-weight:650}
.hero-actions{display:grid;align-content:center;gap:10px}
.subscription-access{display:flex;align-items:center;gap:10px}
.button{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:#fff;color:#1d4ed8;border-radius:12px;padding:12px 17px;text-decoration:none;font:700 14px/1.2 Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;box-shadow:0 10px 22px rgba(15,23,42,.14)}
button.button{border:0;cursor:pointer;font:inherit}.copy-feedback{display:block;min-height:18px;margin-top:6px;font-size:12px;font-weight:600;color:#fff;opacity:.9}.copy-feedback.copy-success{color:#bbf7d0}.copy-feedback.copy-error{color:#fecaca}button.copy-success{background:#dcfce7;color:#166534}button.copy-error{background:#fee2e2;color:#991b1b}
.subscription-qr{display:grid;place-items:center;min-width:132px;padding:10px;border-radius:16px;background:#fff;color:#1e3a8a;box-shadow:0 10px 22px rgba(15,23,42,.14)}.subscription-qr img{display:block;width:104px;height:104px;image-rendering:pixelated}
h2{display:flex;align-items:center;gap:9px;margin:34px 0 13px;font-size:19px}.section-note{margin:0 0 12px;color:var(--muted);font-size:13px}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:34px 0 13px}.section-head h2{margin:0}
.domain-test-form{display:grid;gap:14px}
.domain-input{width:100%;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:#fff;color:var(--text);font:inherit}
.domain-input:focus{outline:2px solid #93c5fd;outline-offset:1px}
.domain-test-result{display:grid;gap:7px;padding:14px;border:1px solid var(--line);border-radius:14px;background:rgba(248,250,252,.85)}
.domain-test-result span{color:var(--muted);font-size:12px;font-weight:700}
.domain-test-result strong{font-size:18px}
.domain-test-result code{overflow-wrap:anywhere}
.domain-test-result small{color:var(--muted)}
.domain-test-result.error strong{color:#b91c1c}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:18px}
.summary-card{background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.2);border-radius:16px;padding:16px;backdrop-filter:blur(10px)}
.summary-card b{display:block;font-size:22px;margin-bottom:6px}.summary-card span{font-size:12px;opacity:.82}
.summary-time{font-size:15px;line-height:1.35;word-break:break-all}
.speed-dots{display:flex;flex-wrap:wrap;align-items:center;gap:5px;min-height:12px;margin-bottom:6px}
.speed-dot{display:inline-block;width:9px;height:9px;border-radius:999px;background:rgba(255,255,255,.42)}
.speed-dot.good{background:#4ade80}.speed-dot.warn{background:#fbbf24}.speed-dot.bad{background:#f87171}.speed-dot.unknown{background:rgba(255,255,255,.42)}
.speed-more{color:#fff;font-size:11px;font-weight:700;opacity:.82}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:0 2px 8px rgba(15,23,42,.04);overflow:hidden}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}th{background:#f8fafc;color:var(--muted);font-size:12px;letter-spacing:.02em}tbody tr:last-child td{border-bottom:0}.node-name{max-width:320px;overflow:hidden;text-overflow:ellipsis}.mono,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.node-actions{display:flex;gap:7px}.node-action{display:grid;place-items:center;width:29px;height:29px;border:1px solid var(--line);border-radius:8px;background:#f8fafc;color:#1d4ed8;cursor:pointer}.node-action svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linejoin:round}.node-action:nth-child(2) svg{fill:currentColor;stroke:none}.node-action:hover{border-color:#93c5fd;background:#eff6ff}.node-qr-modal{width:min(360px,calc(100vw - 32px))}.node-qr-modal .modal-body{display:grid;place-items:center}.node-qr-image{display:block;width:240px;height:240px;image-rendering:pixelated}
.user-agent{max-width:380px;overflow:hidden;text-overflow:ellipsis}
.latency,.status{display:inline-flex;align-items:center;min-width:68px;justify-content:center;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:650}.latency.good,.status.ok{background:#dcfce7;color:#166534}.latency.warn{background:#fef3c7;color:#92400e}.latency.bad,.status.bad{background:#fee2e2;color:#991b1b}.latency.muted,.status.running{background:#e2e8f0;color:#475569}
.group-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
button.group-card{display:flex;flex-direction:column;gap:5px;width:100%;padding:16px 17px;border:1px solid var(--line);border-radius:18px;background:var(--panel);color:var(--text);text-align:left;font:inherit;cursor:pointer;box-shadow:0 2px 8px rgba(15,23,42,.04)}
button.group-card:hover{border-color:#93c5fd;transform:translateY(-1px)}
.group-name{font-size:15px;font-weight:700}.group-meta{color:var(--muted);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:7px}.chip{max-width:100%;overflow:hidden;text-overflow:ellipsis;background:#eef2ff;color:#3730a3;border-radius:999px;padding:5px 10px;font-size:12px}
.providers{display:grid;gap:10px}.provider-card{border:1px solid var(--line);border-radius:14px;padding:12px}.provider-card header{display:flex;justify-content:space-between;gap:10px;align-items:baseline}.provider-card strong{font-size:13px}.provider-card header span{color:var(--muted);font-size:12px;white-space:nowrap}
.payload{list-style:none;margin:10px 0 0;padding:0;max-height:210px;overflow:auto}.payload li{padding:7px 8px;border-top:1px solid var(--line);font-size:12px}.payload code{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.payload-full{max-height:calc(100vh - 180px)}.payload-full code{display:inline;overflow:visible;text-overflow:clip;white-space:normal;word-break:break-all}
.provider-more{margin-top:10px;border:0;border-radius:8px;padding:7px 10px;background:#eef2ff;color:#3730a3;font:inherit;font-size:12px;font-weight:700;cursor:pointer}.provider-more:hover{background:#e0e7ff}
.modal{width:min(880px,calc(100vw - 32px));max-height:min(780px,calc(100vh - 40px));padding:0;border:0;border-radius:18px;background:var(--panel);color:var(--text);box-shadow:0 24px 70px rgba(15,23,42,.28)}
.modal::backdrop{background:rgba(15,23,42,.56);backdrop-filter:blur(3px)}
.modal-panel{display:flex;flex-direction:column;max-height:inherit}
.modal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid var(--line)}
.modal-head strong{display:block;font-size:17px}.modal-head span{display:block;margin-top:4px;color:var(--muted);font-size:12px}
.modal-close{border:0;border-radius:9px;padding:8px 12px;background:#e2e8f0;color:#334155;font:inherit;font-size:12px;font-weight:700;cursor:pointer}
.modal-body{padding:18px 20px;overflow:auto}.modal-body h3{margin:0 0 10px;font-size:13px;color:var(--muted)}.modal-body section+section{margin-top:18px}
pre.yaml{margin:0;padding:18px;max-height:calc(100vh - 150px);overflow:auto;background:#0b1120;color:#e2e8f0;border-radius:0;font-size:12px;line-height:1.65}.muted,.error-text{color:var(--muted)}
@media(max-width:720px){.hero{padding:25px}.subscription-access{width:100%}.summary-card b{font-size:19px}.summary-time{font-size:13px}.table-wrap{margin:0 -20px}.modal{width:calc(100vw - 20px)}.user-agent{max-width:220px}}
@media(prefers-color-scheme:dark){:root{--line:#28324a;--muted:#94a3b8;--panel:#111827;--bg:#080d19;--text:#e6eaf3}th{background:#0f172a}.chip{background:#1e293b;color:#bfdbfe}.button{background:#e2e8f0}.latency.good,.status.ok{background:#14532d;color:#bbf7d0}.latency.warn{background:#78350f;color:#fde68a}.latency.bad,.status.bad{background:#7f1d1d;color:#fecaca}.latency.muted,.status.running{background:#1e293b;color:#cbd5e1}button.group-card,.provider-card{box-shadow:none}.modal-close{background:#1e293b;color:#e2e8f0}.provider-card{background:#0f172a}}
'''
    script = '''
<script>
(function(){
  if(typeof Intl !== "undefined" && Intl.DisplayNames){
    var regionNames = new Intl.DisplayNames(["zh-CN"], {type: "region"});
    document.querySelectorAll("[data-country-code]").forEach(function(cell){
      var code = cell.getAttribute("data-country-code");
      if(code){ try { cell.textContent = regionNames.of(code) || cell.textContent; } catch(error) {} }
    });
  }
  document.querySelectorAll("dialog.modal").forEach(function(dialog){
    dialog.addEventListener("click", function(event){ if(event.target === dialog) dialog.close(); });
  });
  var domainForm = document.querySelector("[data-domain-test-form]");
  var domainResult = document.querySelector("[data-domain-test-result]");
  if(domainForm && domainResult){
    domainForm.addEventListener("submit", function(event){
      event.preventDefault();
      var input = domainForm.querySelector("input[name='domain']");
      var submit = domainForm.querySelector("button[type='submit']");
      var domain = input.value.trim();
      if(!domain) return;
      domainResult.hidden = false;
      domainResult.className = "domain-test-result";
      domainResult.innerHTML = "<span>测试中</span><strong>正在匹配规则…</strong>";
      submit.disabled = true;
      fetch(window.location.pathname + "/test-domain", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({domain: domain})
      }).then(function(response){
        if(!response.ok) throw new Error("测试失败：" + response.status);
        return response.json();
      }).then(function(data){
        domainResult.className = "domain-test-result";
        if(data.matched){
          domainResult.innerHTML = "<span>命中策略</span><strong>" + data.target + "</strong><code>" + data.rule + "</code><small>第 " + (data.ruleIndex + 1) + " 条规则</small>";
        } else {
          domainResult.className = "domain-test-result error";
          domainResult.innerHTML = "<span>测试结果</span><strong>未命中</strong><code>没有规则匹配 " + domain + "</code>";
        }
      }).catch(function(error){
        domainResult.className = "domain-test-result error";
        domainResult.innerHTML = "<span>测试失败</span><strong>" + error.message + "</strong>";
      }).finally(function(){
        submit.disabled = false;
      });
    });
  }
  var button = document.querySelector("[data-copy-subscription]");
  var feedback = document.querySelector("[data-copy-feedback]");
  if(!button) return;
  var originalText = button.textContent;
  function setStatus(kind, message){
    button.textContent = message;
    button.classList.remove("copy-success", "copy-error");
    if(kind) button.classList.add(kind);
    if(feedback){
      feedback.textContent = message;
      feedback.className = "copy-feedback " + (kind || "");
    }
  }
  function resetStatus(){
    button.textContent = originalText;
    button.classList.remove("copy-success", "copy-error");
    if(feedback){
      feedback.textContent = "";
      feedback.className = "copy-feedback";
    }
  }
  function fallbackCopy(text){
    var area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    var copied = false;
    try { copied = document.execCommand("copy"); } catch(error) { copied = false; }
    area.remove();
    return copied;
  }
  document.querySelectorAll("[data-copy-node-link]").forEach(function(nodeButton){
    nodeButton.addEventListener("click", function(){
      var text = nodeButton.getAttribute("data-node-link") || "";
      if(!text) return;
      var originalTitle = nodeButton.title;
      function done(){ nodeButton.title = "已复制"; setTimeout(function(){ nodeButton.title = originalTitle; }, 1600); }
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done).catch(function(){ if(fallbackCopy(text)) done(); });
      } else if(fallbackCopy(text)){ done(); }
    });
  });
  button.addEventListener("click", function(){
    var link = document.querySelector("[data-subscription-link]");
    if(!link){ setStatus("copy-error", "复制失败：未找到订阅链接"); return; }
    var text = link.href;
    setStatus("", "复制中…");
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(function(){
        setStatus("copy-success", "已复制");
        setTimeout(resetStatus, 1800);
      }).catch(function(){
        if(fallbackCopy(text)){
          setStatus("copy-success", "已复制");
          setTimeout(resetStatus, 1800);
        } else {
          setStatus("copy-error", "复制失败，请手动复制");
        }
      });
    } else if(fallbackCopy(text)){
      setStatus("copy-success", "已复制");
      setTimeout(resetStatus, 1800);
    } else {
      setStatus("copy-error", "复制失败，请手动复制");
    }
  });
})();
</script>
'''
    return f'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>订阅详情 · {e(str(details.get("name") or ""))}</title><style>{css}</style></head>
<body><main>
<header class="hero"><div class="hero-inner"><div><p class="eyebrow">AirPlay 聚合订阅</p><h1>{e(str(details.get("name") or "订阅详情"))}</h1><p class="meta">规则组：<b>{e(str(details.get("ruleSetName") or "未关联"))}</b></p></div><div class="subscription-access"><div class="hero-actions"><a class="button" data-subscription-link href="{e(download_url, quote=True)}">下载 AirPlay YAML</a><button class="button" type="button" data-copy-subscription>复制订阅链接</button><span class="copy-feedback" data-copy-feedback role="status" aria-live="polite"></span></div><div class="subscription-qr"><img src="{qr_data_url}" alt="AirPlay YAML 下载链接二维码"></div></div></div>
<section class="summary"><div class="summary-card"><b>{e(str(details.get("mode") or "rule").upper())}</b><span>运行模式</span></div><div class="summary-card"><b>{len(proxies)}</b><div class="speed-dots">{speed_dots}</div><span>输出节点</span></div><div class="summary-card"><b>{len(groups)}</b><span>策略组</span></div><div class="summary-card"><b class="summary-time" title="{e(latest_request_raw or '暂无拉取记录')}">{e(latest_request)}</b><span>最近拉取</span></div></section></header>
<h2>节点</h2><div class="panel"><div class="table-wrap"><table><thead><tr><th>名称</th><th>协议</th><th>服务器</th><th>端口</th><th>国家或地区</th><th>延迟</th><th>导入</th></tr></thead><tbody>{node_rows or '<tr><td colspan="7" class="muted">暂无节点</td></tr>'}</tbody></table></div></div>
<div class="section-head"><h2>策略组</h2><button class="button" type="button" onclick="document.getElementById('domain-test-modal').showModal()">测试域名</button></div><p class="section-note">点击卡片在弹窗中查看成员与该策略组使用的规则内容。</p><div class="group-grid">{''.join(group_cards) or '<p class="muted">暂无策略组</p>'}</div>
<dialog class="modal" id="domain-test-modal" aria-label="测试域名命中"><article class="modal-panel"><header class="modal-head"><div><strong>测试域名命中</strong><span>输入域名，查看最终命中的策略</span></div><button type="button" class="modal-close" onclick="this.closest('dialog').close()">关闭</button></header><div class="modal-body"><form class="domain-test-form" data-domain-test-form><input class="domain-input" type="text" name="domain" placeholder="example.com" autocomplete="off" required><button class="button" type="submit">测试命中</button><div class="domain-test-result" data-domain-test-result hidden></div></form></div></article></dialog>
<div class="section-head"><h2>YAML 预览</h2><button class="button" type="button" onclick="document.getElementById('yaml-modal').showModal()">打开预览</button></div>
<dialog class="modal" id="yaml-modal" aria-label="YAML 预览"><article class="modal-panel"><header class="modal-head"><div><strong>YAML 预览</strong><span>当前发布的 AirPlay 配置</span></div><button type="button" class="modal-close" onclick="this.closest('dialog').close()">关闭</button></header><div class="modal-body"><pre class="yaml"><code>{e(str(details.get("yaml") or ""))}</code></pre></div></article></dialog>
<h2>订阅拉取日志</h2><p class="section-note">记录聚合订阅链接的请求，不包含上游订阅源刷新记录。</p><div class="panel"><div class="table-wrap"><table><thead><tr><th>时间</th><th>客户端 IP</th><th>User-Agent</th><th>状态</th></tr></thead><tbody>{log_rows or '<tr><td colspan="4" class="muted">暂无拉取日志</td></tr>'}</tbody></table></div></div>
{''.join(group_modals)}
{''.join(provider_modals)}
{''.join(node_qr_dialogs)}
</main>{script}</body></html>'''

def mount_extra(app: FastAPI) -> None:
    @app.get("/sub/airplay/details/{token}", include_in_schema=False)
    def public_airplay_subscription_details(token: str, request: Request) -> HTMLResponse:
        details = service.public_subscription_details(token)
        if details is None:
            return HTMLResponse("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>订阅不存在</title><body><h1>订阅不存在或已失效</h1></body></html>", status_code=404)
        download_url = f"{_external_base_url(request)}/sub/airplay/{token}"
        return HTMLResponse(_subscription_details_html(token, details, download_url))

    @app.post("/sub/airplay/details/{token}/test-domain", include_in_schema=False)
    def public_airplay_subscription_domain_test(token: str, payload: DomainTestPayload) -> Response:
        result = service.test_public_subscription_domain(token, payload.domain)
        if result is None:
            return Response(status_code=404)
        return JSONResponse(result)

    @app.get("/sub/airplay/{token}", include_in_schema=False)
    def public_airplay_subscription(token: str, request: Request) -> Response:
        value = service.public_subscription(
            token,
            client_ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
        )
        if value is None: return Response(status_code=404)
        content, digest, created_at, name = value
        # YAML accepts a quoted filename token; keep response headers safe.
        filename = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:80] or "airplay-subscription"
        try:
            last_modified = format_datetime(datetime.fromisoformat(created_at).astimezone(timezone.utc), usegmt=True)
        except ValueError:
            last_modified = created_at
        return Response(content=content, media_type="application/yaml", headers={"Content-Disposition": f'attachment; filename="{filename}.yaml"', "ETag": f'"{digest}"', "Last-Modified": last_modified})
