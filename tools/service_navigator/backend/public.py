from __future__ import annotations

import html
import json
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user
from tools.service_navigator.backend import service

VISITOR_COOKIE_NAME = "service_navigator_visitor"
ASSET_VERSION = "1"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _visitor(request: Request) -> tuple[str, bool]:
    token = request.cookies.get(VISITOR_COOKIE_NAME, "")
    if 20 <= len(token) <= 200:
        return token, False
    return secrets.token_urlsafe(32), True


def _set_visitor(response: Response, token: str, created: bool, request: Request) -> Response:
    if created:
        response.set_cookie(VISITOR_COOKIE_NAME, token, max_age=service.VISITOR_DAYS * 86400, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return response


def _gate(request: Request, slug: str) -> HTMLResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>需要访问权限</title><link rel="stylesheet" href="/tool-assets/service_navigator/public.css?v={ASSET_VERSION}"></head><body class="sn-gate-page"><main class="sn-gate"><h1>此服务导航站为私密站点</h1><p>请使用受邀平台账号登录，或输入站点访问密码。</p><form data-access-password data-endpoint="/service-nav/{quote(slug)}/unlock"><label>访问密码<input required name="password" type="password" autocomplete="current-password"></label><button>使用密码进入</button></form><div class="sn-divider">或</div><form data-platform-login><label>用户名<input required name="username" autocomplete="username"></label><label>平台密码<input required name="password" type="password" autocomplete="current-password"></label><button>登录并继续</button></form><p class="sn-error" data-error></p></main><script>const error=document.querySelector('[data-error]');for(const form of document.querySelectorAll('form'))form.addEventListener('submit',async(e)=>{{e.preventDefault();error.textContent='';const endpoint=form.hasAttribute('data-platform-login')?'/api/auth/login':form.dataset.endpoint;const r=await fetch(endpoint,{{method:'POST',credentials:'include',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(Object.fromEntries(new FormData(form)))}});if(r.ok)location.href={json.dumps(target)};else{{const b=await r.json().catch(()=>({{}}));error.textContent=b.error?.message||'认证失败';}}}});</script></body></html>'''
    return HTMLResponse(page, status_code=401)


def _service_card(item: dict) -> str:
    title = item["name"]
    protocol = item["serviceName"] or "unknown"
    offline = " is-offline" if item["state"] == "offline" else ""
    state = "离线（上次扫描）" if item["state"] == "offline" else "在线"
    icon = f'<img class="sn-service-icon" src="{_esc(item["faviconUrl"])}" alt="" loading="lazy">' if item.get("faviconUrl") else '<span class="sn-service-icon sn-service-icon-fallback">◌</span>'
    detail = f'{_esc(item["targetLabel"])} · {_esc(item["targetAddress"])}:{item["port"]}'
    description = f'<p class="sn-service-description">{_esc(item["description"])}</p>' if item.get("description") else ""
    if item.get("url"):
        action = f'<a class="sn-open" href="{_esc(item["url"])}" target="_blank" rel="noreferrer">打开服务 ↗</a>'
    else:
        action = f'<button class="sn-copy" type="button" data-command="{_esc(item["command"])}">复制连接命令</button>'
    search = _esc(" ".join([title, protocol, item["targetLabel"], item["targetAddress"], item.get("category", "")]))
    category = item.get("category") or "未分类"
    category_tag = f'<span>{_esc(item["category"])}</span>' if item.get("category") else ""
    return f'<article class="sn-service{offline}" data-service-card data-search="{search}" data-category="{_esc(category)}" data-target="{_esc(item["targetId"])}"><header>{icon}<div><h2>{_esc(title)}</h2><p>{detail}</p></div><span class="sn-state">{state}</span></header><div class="sn-service-meta"><span>{_esc(protocol)}</span><span>TCP/{item["port"]}</span>{category_tag}</div>{description}<footer>{action}</footer></article>'


def render_site(site: dict, principal: dict) -> HTMLResponse:
    services = service.public_services(site)
    categories = sorted({item.get("category") or "未分类" for item in services})
    targets = sorted({(item["targetId"], item["targetLabel"]) for item in services}, key=lambda pair: pair[1])
    cards = "".join(_service_card(item) for item in services) or '<p class="sn-empty">尚未发现可展示的服务。</p>'
    category_buttons = '<button class="sn-filter is-active" data-filter-category="">全部</button>' + "".join(f'<button class="sn-filter" data-filter-category="{_esc(category)}">{_esc(category)}</button>' for category in categories)
    target_options = '<option value="">全部目标</option>' + "".join(f'<option value="{_esc(target_id)}">{_esc(label)}</option>' for target_id, label in targets)
    identity = ""
    if principal.get("kind") in {"owner", "user", "password"}:
        label = "Guest" if principal.get("kind") == "password" else str(principal.get("label") or "")
        endpoint = f'/service-nav/{quote(site["slug"])}/logout' if principal.get("kind") == "password" else "/api/auth/logout"
        identity = f'<button class="sn-identity" data-logout="{_esc(endpoint)}" title="退出认证">{_esc(label)} · 退出</button>'
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{_esc(site["description"])}"><title>{_esc(site["title"])} · 服务导航</title><link rel="stylesheet" href="/tool-assets/service_navigator/public.css?v={ASSET_VERSION}"></head><body><main class="sn-public-shell"><header class="sn-public-head"><div><p class="sn-eyebrow">SERVICE NAVIGATOR</p><h1>{_esc(site["title"])}</h1><p>{_esc(site["description"] or "扫描发现的网络服务导航")}</p></div>{identity}</header><section class="sn-controls"><label class="sn-search">⌕<input data-search-input placeholder="搜索服务、协议或目标"></label><select data-target-select>{target_options}</select><div class="sn-filters">{category_buttons}</div></section><p class="sn-result-count" data-result-count></p><section class="sn-service-grid" data-service-grid>{cards}</section></main><script type="module" src="/tool-assets/service_navigator/public.js?v={ASSET_VERSION}"></script></body></html>'''
    return HTMLResponse(page)


def mount_extra(app: FastAPI) -> None:
    def accessible(request: Request, slug: str) -> tuple[dict | None, dict, str, bool]:
        site = service.public_site(slug)
        token, created = _visitor(request)
        principal = service.site_access(site, get_optional_user(request), token) if site else {"allowed": False}
        return site, principal, token, created

    @app.get("/service-nav/icon/{service_id}", include_in_schema=False)
    def icon(request: Request, service_id: str):
        site = service.public_site_for_icon(service_id)
        token, _created = _visitor(request)
        if not site or not service.site_access(site, get_optional_user(request), token)["allowed"]:
            return Response(status_code=404)
        path = service.public_icon(service_id, site)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}) if path else Response(status_code=404)

    @app.post("/service-nav/{slug}/unlock", include_in_schema=False)
    def unlock(request: Request, slug: str, payload: dict = Body(...)):
        site = service.public_site(slug)
        if not site:
            raise ToolboxError("SITE_NOT_FOUND", "服务导航站不存在", status_code=404)
        token, created = _visitor(request)
        service.unlock_site(site, str(payload.get("password") or ""), token)
        return _set_visitor(JSONResponse({"authenticated": True}), token, created, request)

    @app.post("/service-nav/{slug}/logout", include_in_schema=False)
    def logout(request: Request, slug: str):
        site = service.public_site(slug)
        if not site:
            raise ToolboxError("SITE_NOT_FOUND", "服务导航站不存在", status_code=404)
        token, created = _visitor(request)
        service.lock_site(site, token)
        return _set_visitor(JSONResponse({"authenticated": False}), token, created, request)

    @app.get("/service-nav/{slug}", include_in_schema=False)
    def page(request: Request, slug: str):
        site, principal, token, created = accessible(request, slug)
        if not site:
            return HTMLResponse("<!doctype html><title>404</title><h1>服务导航站不存在</h1>", status_code=404)
        if not principal["allowed"]:
            return _set_visitor(_gate(request, slug), token, created, request)
        return _set_visitor(render_site(site, principal), token, created, request)
