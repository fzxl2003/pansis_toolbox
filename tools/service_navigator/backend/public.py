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
ASSET_VERSION = "50"


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


def _gate(request: Request, slug: str, site: dict) -> HTMLResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    theme = service._normalise_site_theme(site.get("theme", "auto"))
    accent_color = service._normalise_accent_color(site.get("accent_color", "#4f7cff"))
    if theme == "background":
        theme = "auto"
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>需要访问权限</title><link rel="stylesheet" href="/tool-assets/service_navigator/public.css?v={ASSET_VERSION}"></head><body class="sn-gate-page sn-dashboard-body sn-theme-{_esc(theme)}" style="--sn-user-accent:{_esc(accent_color)}"><main class="sn-gate"><h1>此服务导航站为私密站点</h1><p>请使用受邀平台账号登录，或输入站点访问密码。</p><form data-access-password data-endpoint="/service-nav/{quote(slug)}/unlock"><label>访问密码<input required name="password" type="password" autocomplete="current-password"></label><button>使用密码进入</button></form><div class="sn-divider">或</div><form data-platform-login><label>用户名<input required name="username" autocomplete="username"></label><label>平台密码<input required name="password" type="password" autocomplete="current-password"></label><button>登录并继续</button></form><p class="sn-error" data-error></p></main><script>const error=document.querySelector('[data-error]');for(const form of document.querySelectorAll('form'))form.addEventListener('submit',async(e)=>{{e.preventDefault();error.textContent='';const endpoint=form.hasAttribute('data-platform-login')?'/api/auth/login':form.dataset.endpoint;const r=await fetch(endpoint,{{method:'POST',credentials:'include',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(Object.fromEntries(new FormData(form)))}});if(r.ok)location.href={json.dumps(target)};else{{const b=await r.json().catch(()=>({{}}));error.textContent=b.error?.message||'认证失败';}}}});</script></body></html>'''
    return HTMLResponse(page, status_code=401)


def render_site(site: dict, principal: dict) -> HTMLResponse:
    owner = principal.get("kind") == "owner"
    navigation = service.public_navigation(site, include_icons=owner)
    target_pages = "".join(f'<div class="sn-page-entry sn-target-page{" sn-page-hidden" if not target["visible"] else ""}"><button class="sn-page-link" data-page="{_esc(target["id"])}" title="{_esc(target["name"])}">{_esc(target["name"])}</button><div class="sn-page-order"><button data-target-move="up" data-target-id="{_esc(target["targetId"])}" title="上移" aria-label="上移">↑</button><button data-target-move="down" data-target-id="{_esc(target["targetId"])}" title="下移" aria-label="下移">↓</button></div><button class="sn-page-edit" data-target-edit="{_esc(target["targetId"])}" title="编辑扫描目标" aria-label="编辑扫描目标">✎</button></div>' for target in navigation["targetPages"] if owner or target["visible"])
    pages = "".join(f'<div class="sn-page-entry{" sn-page-hidden" if not page.get("visible", True) else ""}" data-page-entry="{_esc(page["id"])}"><button class="sn-page-link" data-page="{_esc(page["id"])}" title="{_esc(page["name"])}">{_esc(page["name"])}</button><div class="sn-page-order"><button data-page-move="up" data-page-id="{_esc(page["id"])}" title="上移" aria-label="上移">↑</button><button data-page-move="down" data-page-id="{_esc(page["id"])}" title="下移" aria-label="下移">↓</button></div><button class="sn-page-edit" data-page-edit="{_esc(page["id"])}" title="编辑页面" aria-label="编辑页面">✎</button></div>' for page in navigation["pages"] if owner or page.get("visible", True))
    background = f"/service-nav/background/{quote(site['slug'])}" if site.get("background_source") in {"custom", "bing"} else ""
    background_style = f"--sn-user-background:url('{_esc(background)}');" if background else ""
    theme = service._normalise_site_theme(site.get("theme", "auto"))
    accent_color = service._normalise_accent_color(site.get("accent_color", "#4f7cff"))
    card_opacity = service._normalise_card_opacity(site.get("card_opacity", 84))
    card_blur = service._normalise_card_blur(site.get("card_blur", 1))
    background_overlay_opacity = service._normalise_background_overlay_opacity(site.get("background_overlay_opacity", 50))
    navigation["appearance"] = {
        "theme": theme,
        "accentColor": accent_color,
        "accentColorMode": "background" if site.get("accent_color_mode") == "background" else "custom",
        "backgroundUrl": background,
        "cardOpacity": card_opacity,
        "cardBlur": card_blur,
        "backgroundOverlayOpacity": background_overlay_opacity,
        "backgroundSource": str(site.get("background_source") or "default"),
    }
    # Version the complete ES module graph; versioning just public.js leaves
    # its imported modules cached after an update.
    module_import_map = json.dumps({"imports": {
        f"/tool-assets/service_navigator/public/{path.name}":
        f"/tool-assets/service_navigator/public/{path.name}?v={ASSET_VERSION}"
        for path in (Path(__file__).resolve().parents[1] / "assets" / "public").glob("*.js")
    }})
    navigation_json = json.dumps(navigation, ensure_ascii=False).replace("</", "<\\/")
    page_add = '<button class="sn-page-add" data-add-page type="button" title="新增页面" aria-label="新增页面">+</button>' if principal.get("kind") == "owner" else ""
    owner_controls = '<div class="sn-owner-controls" data-owner-controls><div class="sn-owner-control-actions"><button class="sn-owner-edit" data-toggle-page-edit>编辑导航</button><button class="sn-owner-edit" data-page-canvas-edit>编辑页面</button><button class="sn-owner-edit" data-page-icons>管理图标</button><button class="sn-owner-edit" data-open-editor>外观与背景</button></div><button class="sn-owner-controls-toggle" data-toggle-owner-controls type="button" title="隐藏编辑工具" aria-label="隐藏编辑工具"><span></span><span></span></button></div>' if principal.get("kind") == "owner" else ""
    exit_editing = '<button class="sn-edit-exit sn-editor-save" data-exit-editing type="button" hidden>退出编辑</button>' if owner else ""
    identity = ""
    if principal.get("kind") in {"owner", "user", "password"}:
        label = "Guest" if principal.get("kind") == "password" else str(principal.get("label") or "")
        endpoint = f'/service-nav/{quote(site["slug"])}/logout' if principal.get("kind") == "password" else "/api/auth/logout"
        identity = f'<button class="sn-identity" data-logout="{_esc(endpoint)}" title="退出认证">{_esc(label)} · 退出</button>'
    elif principal.get("kind") == "anonymous":
        identity = '<button class="sn-login-avatar" data-login type="button" title="登录" aria-label="登录"></button>'
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{_esc(site["description"])}"><title>{_esc(site["title"])} · 服务导航</title><link rel="stylesheet" href="/tool-assets/service_navigator/public.css?v={ASSET_VERSION}"></head><body class="sn-dashboard-body sn-theme-{_esc(theme)}" data-owner="{str(principal.get("kind") == "owner").lower()}" data-background-source="{_esc(str(site.get("background_source") or "default"))}" style="{background_style}--sn-user-accent:{_esc(accent_color)};--sn-card-opacity:{card_opacity};--sn-background-overlay-opacity:{background_overlay_opacity}%"><main class="sn-dashboard"><aside class="sn-sidebar"><div class="sn-sidebar-top"><a class="sn-brand" href="/service-nav/{_esc(site["slug"])}" aria-label="返回服务导航首页"><span>◈</span><strong>{_esc(site["title"])}</strong></a><button class="sn-sidebar-toggle" data-toggle-sidebar type="button" aria-label="收起导航栏" title="收起导航栏"><span></span><span></span></button></div><nav aria-label="导航页面">{target_pages}{pages}{page_add}</nav><div class="sn-sidebar-footer">{owner_controls}{identity}{exit_editing}</div></aside><section class="sn-dashboard-main"><header class="sn-dashboard-head"><time class="sn-clock" aria-label="当前时间"><strong data-clock-time></strong><small data-clock-date></small></time></header><div class="sn-search-area" role="search"><div class="sn-search-row"><button class="sn-engine-trigger" data-engine-trigger type="button" aria-haspopup="true" aria-expanded="false" aria-label="选择搜索引擎" title="选择搜索引擎"><span class="sn-engine-mark sn-engine-google" data-selected-engine>G</span><span class="sn-engine-caret" aria-hidden="true">⌄</span></button><label class="sn-dashboard-search"><input data-search-input type="search" placeholder="输入搜索内容"></label><button class="sn-web-search" data-web-search type="button" aria-label="搜索" title="搜索"><span aria-hidden="true"></span></button></div><div class="sn-engine-menu" data-engine-menu hidden><button type="button" data-engine="baidu"><span class="sn-engine-mark sn-engine-baidu">⌘</span><strong>百度</strong></button><button type="button" data-engine="google"><span class="sn-engine-mark sn-engine-google">G</span><strong>Google</strong></button><button type="button" data-engine="bing"><span class="sn-engine-mark sn-engine-bing">B</span><strong>必应</strong></button></div></div><section data-navigation-content></section></section></main><div class="sn-public-modal" data-action-modal hidden><div class="sn-public-modal-card" role="dialog" aria-modal="true"><button class="sn-modal-close" data-close-modal aria-label="关闭">×</button><div data-modal-content></div></div></div><script id="sn-navigation-data" type="application/json">{navigation_json}</script><script type="importmap">{module_import_map}</script><script type="module" src="/tool-assets/service_navigator/public.js?v={ASSET_VERSION}"></script></body></html>'''
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

    @app.get("/service-nav/navigation-icon/{icon_id}", include_in_schema=False)
    def navigation_icon(request: Request, icon_id: str):
        site = service.public_site_for_navigation_icon(icon_id)
        token, _created = _visitor(request)
        if not site or not service.site_access(site, get_optional_user(request), token)["allowed"]:
            return Response(status_code=404)
        path = service.public_navigation_icon(icon_id, site)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}) if path else Response(status_code=404)

    @app.get("/service-nav/background/{slug}", include_in_schema=False)
    def background(request: Request, slug: str):
        site = service.public_site(slug)
        token, _created = _visitor(request)
        if not site or not service.site_access(site, get_optional_user(request), token)["allowed"]:
            return Response(status_code=404)
        path = service.public_background(site)
        return FileResponse(path, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}) if path else Response(status_code=404)

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
            return _set_visitor(_gate(request, slug, site), token, created, request)
        return _set_visitor(render_site(site, principal), token, created, request)
