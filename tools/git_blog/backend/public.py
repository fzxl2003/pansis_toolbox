from __future__ import annotations

import html
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urlencode

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user
from tools.git_blog.backend import service

ASSET_VERSION = "public-share-auth-20260929"
DIRECTORY_COOKIE_NAME = "git_blog_directory_collapsed"
VISITOR_COOKIE_NAME = "git_blog_visitor"


def _site(blog: dict) -> dict:
    configured_site = blog["config"]["site"]
    accent = configured_site.get("accentColor", "#42b983") if configured_site.get("accentColorEnabled") else "#42b983"
    config = {
        **(blog.get("effectiveConfig") or blog["config"])["site"],
        "customTemplate": configured_site.get("customTemplate", False),
        "customThemeId": configured_site.get("customThemeId", ""),
        "accentColor": accent,
    }
    return {**config, "title": blog["name"], "description": config.get("description") or "GitHub Markdown 博客"}


def _esc(value: object) -> str: return html.escape(str(value or ""), quote=True)


def _url(request: Request, path: str) -> str:
    return str(request.base_url).rstrip("/") + path


def _relative_time(value: object) -> str:
    """Format an ISO timestamp for article cards without exposing clock noise."""
    raw = str(value or "")
    try:
        date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        now = datetime.now(date.tzinfo) if date.tzinfo else datetime.now()
        seconds = max(0, int((now - date).total_seconds()))
    except (TypeError, ValueError):
        return raw[:10]
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 7 * 86400:
        return f"{seconds // 86400} 天前"
    return date.strftime("%Y-%m-%d")


def _time(value: object) -> str:
    raw = str(value or "")
    return f'<time class="blog-time" datetime="{_esc(raw)}" data-time="{_esc(raw)}">{_esc(_relative_time(raw))}</time>'


def _back_to_top_button(extra_class: str = "") -> str:
    classes = f"blog-back-to-top {extra_class}".strip()
    return f'<button class="{classes}" type="button" aria-label="返回页面开头" title="返回开头"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6-6 6 6M12 4v16"></path></svg></button>'


def _page_url(request: Request, page: int) -> str:
    params = {key: value for key, value in request.query_params.items() if key != "page"}
    params["page"] = str(page)
    return "?" + urlencode(params)


def _pagination(request: Request, page: int, total: int, *, page_size: int = 50) -> str:
    total_pages = max(1, (total + page_size - 1) // page_size)
    if total_pages <= 1:
        return ""
    candidates = {1, total_pages, *range(max(1, page - 2), min(total_pages, page + 2) + 1)}
    pieces: list[str] = []
    previous = f'<a class="blog-page-control" href="{_esc(_page_url(request, page - 1))}" aria-label="上一页">‹</a>' if page > 1 else '<span class="blog-page-control is-disabled" aria-hidden="true">‹</span>'
    pieces.append(previous)
    last = 0
    for number in sorted(candidates):
        if number - last > 1:
            pieces.append('<span class="blog-page-ellipsis" aria-hidden="true">…</span>')
        current = ' aria-current="page"' if number == page else ''
        active = ' is-current' if number == page else ''
        pieces.append(f'<a class="blog-page-number{active}" href="{_esc(_page_url(request, number))}"{current}>{number}</a>')
        last = number
    following = f'<a class="blog-page-control" href="{_esc(_page_url(request, page + 1))}" aria-label="下一页">›</a>' if page < total_pages else '<span class="blog-page-control is-disabled" aria-hidden="true">›</span>'
    pieces.append(following)
    return f'<nav class="blog-pagination" aria-label="文章分页">{"".join(pieces)}</nav>'


def _article_card(article: dict, base: str, *, show_meta: bool = True) -> str:
    href = f'{base}/posts/{quote(article["slug"])}'
    tags = ' '.join(f'<a href="{base}/tags/{quote(tag)}">#{_esc(tag)}</a>' for tag in article["tags"])
    cover = article["coverPath"]
    if cover and not cover.startswith(("http://", "https://")):
        cover = f'{base}/assets/{quote(cover.lstrip("/"))}'
    image = f'<img class="blog-cover" src="{_esc(cover)}" alt="" loading="lazy">' if cover else ""
    meta = f'<p class="blog-meta">{_time(article["publishedAt"])} {tags}</p>' if show_meta else ""
    return f'<li class="blog-card" data-href="{_esc(href)}" tabindex="0" role="link" aria-label="阅读：{_esc(article["title"])}"><div><h2><a href="{_esc(href)}">{_esc(article["title"])}</a></h2>{meta}<p class="blog-summary">{_esc(article["summary"])}</p></div>{image}</li>'


def _identity_control(blog: dict, principal: dict[str, object]) -> str:
    kind = str(principal.get("kind") or "anonymous")
    if kind not in {"owner", "user", "password"}:
        return ""
    is_password = kind == "password"
    label = "Guest" if is_password else str(principal.get("label") or "")
    endpoint = f'/blog/{quote(blog["slug"])}/logout' if is_password else "/api/auth/logout"
    action = "退出认证" if is_password else "退出登录"
    return f'''<details class="blog-identity" data-blog-identity data-logout-endpoint="{_esc(endpoint)}"><summary aria-label="当前访问身份：{_esc(label)}"><span class="blog-identity-avatar" aria-hidden="true">{_esc(label[:1].upper())}</span><span>{_esc(label)}</span></summary><div class="blog-identity-menu"><button type="button" data-blog-logout>{action}</button><small data-blog-logout-status></small></div></details>'''


def _layout(request: Request, blog: dict, title: str, body: str, *, description: str = "", not_found: bool = False, article_theme: bool = False, include_nav: bool = True, noindex: bool = False, resource_base: str = "", principal: dict[str, object] | None = None) -> HTMLResponse:
    site = _site(blog); base = f"/blog/{quote(blog['slug'])}"; css = "/tool-assets/git_blog"
    asset_base = resource_base or base
    template_css = f'<link rel="stylesheet" href="{asset_base}/theme/style.css">' if site.get("customTemplate") else ""
    typora_css = f'<link rel="stylesheet" href="{asset_base}/custom-theme.css">' if article_theme and site.get("customThemeId") else ""
    custom = template_css + typora_css
    body_theme = "custom" if article_theme and site.get("customThemeId") else str(site.get("theme", "auto"))
    page_kind = "blog-article-page" if article_theme else "blog-overview-page"
    robots = '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    head = f'''<!doctype html><html lang="{_esc(site.get('language','zh-CN'))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)} · {_esc(site['title'])}</title><meta name="description" content="{_esc(description or site['description'])}">{robots}<link rel="canonical" href="{_esc(_url(request, request.url.path))}"><meta property="og:title" content="{_esc(title)}"><meta property="og:description" content="{_esc(description or site['description'])}"><link rel="stylesheet" href="{css}/vendor/github-markdown.css"><link rel="stylesheet" href="{css}/vendor/katex.min.css"><link rel="stylesheet" href="{css}/vendor/highlight.css"><link rel="stylesheet" href="{css}/blog.css?v={ASSET_VERSION}">{custom}<style>:root{{--blog-user-accent:{_esc(site.get('accentColor','#42b983'))};--blog-width:{int(site.get('contentWidth',860))}px;--blog-font:{_esc(site.get('fontFamily','Ubuntu, Source Sans Pro, sans-serif'))};}}</style></head>'''
    icons=f'<a class="blog-icon" title="RSS" href="{base}/feed.xml">◔</a><a class="blog-icon" title="Atom" href="{base}/atom.xml">◉</a>'
    if principal is None:
        principal = service.blog_access(blog, get_optional_user(request), request.cookies.get(VISITOR_COOKIE_NAME, ""))
    identity = _identity_control(blog, principal)
    nav=f'<header class="blog-head"><a href="{base}" class="blog-brand">{_esc(site["title"])}</a><nav class="blog-nav"><a href="{base}">首页</a><a href="{base}/directory">目录</a><a href="{base}/tags">标签</a></nav><span class="blog-subscribe">{icons}</span>{identity}</header>'
    return HTMLResponse(head+f'<body class="git-blog-page theme-{_esc(body_theme)} {page_kind}"><main class="blog-shell">{nav if include_nav else ""}{body}</main><script type="module" src="{css}/public.js?v={ASSET_VERSION}"></script></body></html>', status_code=404 if not_found else 200)


def _visitor(request: Request) -> tuple[str, bool]:
    existing = request.cookies.get(VISITOR_COOKIE_NAME, "")
    if 20 <= len(existing) <= 200:
        return existing, False
    return secrets.token_urlsafe(32), True


def _set_visitor(response: Response, token: str, created: bool, request: Request) -> Response:
    if created:
        response.set_cookie(VISITOR_COOKIE_NAME, token, max_age=service.VISITOR_DAYS * 86400, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return response


def _gate_page(request: Request, *, title: str, message: str, blog_slug: str = "", share_token: str = "", status_code: int = 401) -> HTMLResponse:
    target = request.url.path + (f'?{request.url.query}' if request.url.query else '')
    blog_form = f'''<form data-blog-unlock data-endpoint="/blog/{quote(blog_slug)}/unlock"><h2>使用博客访问密码</h2><input name="password" type="password" autocomplete="current-password" placeholder="访问密码" required><button>进入博客</button></form>''' if blog_slug else ""
    share_form = f'''<form data-share-unlock data-endpoint="/blog/share/{quote(share_token)}/unlock"><h2>输入分享密码</h2><input name="password" type="password" autocomplete="current-password" placeholder="分享密码" required><button>打开文档</button></form>''' if share_token else ""
    login_form = f'''<form data-platform-login><h2>使用平台账号</h2><input name="username" autocomplete="username" placeholder="用户名" required><input name="password" type="password" autocomplete="current-password" placeholder="密码" required><button>登录并继续</button></form>''' if blog_slug else ""
    source = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{_esc(title)}</title><style>body{{margin:0;background:#f8fafc;color:#1e293b;font-family:system-ui}}main{{width:min(92%,520px);margin:8vh auto}}section{{padding:26px;border:1px solid #e2e8f0;border-radius:14px;background:white;box-shadow:0 16px 40px #0f172a12}}h1{{margin:0 0 8px;font-size:24px}}h2{{margin:18px 0 8px;font-size:14px}}p{{color:#64748b}}form{{display:grid;gap:9px}}input,button{{box-sizing:border-box;min-height:42px;padding:9px 12px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}}button{{border-color:#4f46e5;background:#4f46e5;color:white;cursor:pointer}}[data-error]{{min-height:20px;color:#b91c1c;font-size:13px}}</style></head><body><main><section><h1>{_esc(title)}</h1><p>{_esc(message)}</p>{share_form}{blog_form}{login_form}<div data-error></div></section></main><script>const error=document.querySelector('[data-error]');for(const form of document.querySelectorAll('form'))form.addEventListener('submit',async event=>{{event.preventDefault();error.textContent='';const data=Object.fromEntries(new FormData(form));const endpoint=form.hasAttribute('data-platform-login')?'/api/auth/login':form.dataset.endpoint;const response=await fetch(endpoint,{{method:'POST',credentials:'include',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});if(response.ok)location.href={json.dumps(target)};else{{const body=await response.json().catch(()=>({{}}));error.textContent=body.error?.message||'认证失败';}}}});</script></body></html>'''
    return HTMLResponse(source, status_code=status_code)


def _article_body(blog: dict, item: dict, *, share_button: str = "") -> str:
    article_class = "typora-export" if _site(blog).get("customThemeId") else "markdown-body typora-export"
    return f'{share_button}<section class="blog-article-layout"><div class="blog-outline-rail"><button class="blog-outline-toggle" type="button" aria-label="显示目录" title="显示目录" aria-expanded="true" aria-controls="blog-outline"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg></button><button class="blog-outline-backdrop" type="button" aria-label="点击页面收起目录" tabindex="-1"></button><aside class="blog-outline" id="blog-outline" aria-label="文章大纲"><div class="blog-outline-head"><strong>目录</strong><button type="button" class="blog-outline-close" aria-label="收起目录" title="收起目录"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg></button></div><nav class="blog-outline-list"></nav></aside></div><article id="write" class="{article_class}">{item["html"]}</article>{_back_to_top_button()}</section>'


def _share_control(blog: dict, item: dict, principal: dict[str, object]) -> str:
    if not blog.get("shareEnabled"):
        return ""
    can_share = bool(principal.get("canShare"))
    needs_auth = not can_share and blog.get("visibility") == "public" and principal.get("kind") == "anonymous"
    if not can_share and not needs_auth:
        return ""
    auth_dialog = ""
    if needs_auth:
        auth_dialog = '''<dialog class="blog-share-dialog blog-share-auth-dialog" data-share-auth-dialog><div class="blog-share-form"><header><div><strong>需要分享权限</strong><small>请选择一种认证方式继续</small></div><button type="button" data-share-auth-close aria-label="关闭">×</button></header><form data-share-blog-auth-form><label>博客访问密码<input name="password" type="password" autocomplete="current-password" placeholder="输入具有分享权限的访问密码" required></label><button>验证访问密码</button></form><div class="blog-share-divider"><span>或</span></div><form data-share-platform-auth-form><label>平台用户名<input name="username" autocomplete="username" required></label><label>平台密码<input name="password" type="password" autocomplete="current-password" required></label><button>登录平台账号</button></form><div data-share-auth-result></div></div></dialog>'''
    default_expiry = (datetime.now().astimezone() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
    return f'''<div class="blog-share" data-share-root data-can-share="{str(can_share).lower()}" data-endpoint="/blog/{quote(blog['slug'])}/shares" data-access-endpoint="/blog/{quote(blog['slug'])}/share-access" data-article="{_esc(item['slug'])}"><button class="blog-share-button" type="button" aria-label="分享文档" title="分享文档"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><path d="m8.6 10.5 6.8-4M8.6 13.5l6.8 4"></path></svg></button><dialog class="blog-share-dialog" data-share-dialog><form class="blog-share-form"><header><div><strong>分享文档</strong><small>设置链接的访问方式和有效范围</small></div><button type="button" data-share-close aria-label="关闭">×</button></header><label>展示方式<select name="mode"><option value="document">仅显示当前文档</option><option value="full">显示完整博客界面</option></select></label><label>有效期<input name="expiresAt" type="datetime-local" value="{default_expiry}"></label><label>浏览器打开上限<input name="maxViews" type="number" min="1" placeholder="留空表示不限"></label><label>分享密码（可选）<input name="password" type="password" autocomplete="new-password" placeholder="留空表示无需密码"></label><button type="submit" data-create-share>创建分享链接</button><div data-share-result></div></form></dialog>{auth_dialog}</div>'''


def _missing(request: Request, slug: str = "") -> HTMLResponse:
    if slug and (blog := service.public_blog(slug)):
        return _layout(request,blog,"页面不存在",'<section class="blog-not-found"><h2>404</h2><p>请求的内容不存在或尚未发布。</p></section>',not_found=True)
    return HTMLResponse('<!doctype html><title>博客不存在</title><main style="font-family:system-ui;text-align:center;padding:5rem"><h1>404</h1><p>博客不存在或暂不可访问。</p></main>',status_code=404)


def _list(request: Request, blog: dict, *, tag: str = "", archive: bool = False, principal: dict[str, object] | None = None) -> HTMLResponse:
    page=max(1,int(request.query_params.get("page","1") or 1)); query=request.query_params.get("q","").strip()[:100]
    articles,total=service.public_articles(blog["id"],page=page,tag=tag,query=query)
    base=f"/blog/{quote(blog['slug'])}"; items=[]
    for article in articles:
        items.append(_article_card(article, base))
    title = f"标签：{tag}" if tag else ("归档" if archive else "")
    search=f'<form class="blog-search" action="{base}" method="get"><input name="q" value="{_esc(query)}" placeholder="搜索文章"><button>搜索</button></form>' if not archive else ""
    tags=' '.join(f'<a href="{base}/tags/{quote(t)}">#{_esc(t)}</a>' for t in service.public_tags(blog["id"])) if tag else ''
    heading=f'<h2>{_esc(title)}</h2>' if title else ''
    back_to_top = _back_to_top_button("blog-home-back-to-top") if not tag and not archive else ""
    body=f'{heading}{search}<p class="blog-meta">{tags}</p><ul class="blog-list">{"".join(items) or "<li>暂无已发布文章。</li>"}</ul>{_pagination(request,page,total)}{back_to_top}'
    return _layout(request,blog,title,body,description=_site(blog)["description"],principal=principal)


def _collapsed_directories(request: Request) -> set[str]:
    raw = request.cookies.get(DIRECTORY_COOKIE_NAME, "")
    if not raw or len(raw) > 4096:
        return set()
    try:
        paths = json.loads(unquote(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()
    if not isinstance(paths, list):
        return set()
    return {path for path in paths[:100] if isinstance(path, str) and 0 < len(path) <= 500}


def _directory_body(blog: dict, selected: str = "", collapsed: set[str] | None = None) -> str:
    base = f'/blog/{quote(blog["slug"])}'
    collapsed = collapsed or set()
    def node(item: dict, parent: str = "") -> str:
        path = f'{parent}/{item["name"]}'.strip('/')
        children = ''.join(node(child, path) for child in item["children"] if child["children"])
        if not item["children"]:
            return ""
        link = f'<a href="{base}/directory?path={quote(path)}">{_esc(item["name"])}</a>'
        open_attr = "" if path in collapsed else " open"
        return f'<li><details data-directory-path="{_esc(path)}"{open_attr}><summary>{link}</summary><ul>{children}</ul></details></li>' if children else f'<li class="blog-folder-leaf">{link}</li>'
    articles,_ = service.public_articles(blog["id"], path_prefix=selected)
    cards=''.join(_article_card(article, base, show_meta=False) for article in articles)
    directory = "".join(node(item) for item in service.public_directory(blog["id"])["children"])
    return f'<section class="blog-directory-layout"><div class="blog-directory-rail"><button class="blog-directory-toggle" type="button" aria-label="显示文件目录" title="显示文件目录" aria-expanded="true" aria-controls="blog-directory-panel"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg></button><button class="blog-directory-backdrop" type="button" aria-label="点击页面收起文件目录" tabindex="-1"></button><aside class="blog-directory" id="blog-directory-panel" data-cookie-path="{_esc(base)}"><div class="blog-directory-head"><strong>目录</strong><button class="blog-directory-close" type="button" aria-label="收起文件目录" title="收起文件目录"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg></button></div><ul>{directory}</ul></aside></div><section class="blog-directory-content"><h2>{_esc(selected or "全部文档")}</h2><ul class="blog-list">{cards or "<li>暂无文档。</li>"}</ul></section>{_back_to_top_button()}</section>'


def mount_extra(app: FastAPI) -> None:
    @app.get("/blog", include_in_schema=False)
    def blog_about() -> HTMLResponse:
        return HTMLResponse('<!doctype html><title>Git 博客</title><main style="font-family:system-ui;max-width:720px;margin:4rem auto;padding:0 1rem"><h1>Git 博客</h1><p>这是由 Pansis Toolbox 托管的 Markdown 博客服务。</p></main>')

    def normal_access(request: Request, blog_slug: str) -> tuple[dict | None, dict, str, bool]:
        blog = service.public_blog(blog_slug)
        visitor, created = _visitor(request)
        principal = service.blog_access(blog, get_optional_user(request), visitor) if blog else {"allowed": False, "canShare": False}
        return blog, principal, visitor, created

    def protected_blog(request: Request, blog_slug: str) -> tuple[dict | None, Response | None, dict, str]:
        blog, principal, visitor, created = normal_access(request, blog_slug)
        if not blog:
            return None, _missing(request), principal, visitor
        if not principal["allowed"]:
            gate = _gate_page(request, title="此博客为私密博客", message="请使用受邀平台账号或博客访问密码进入。", blog_slug=blog_slug)
            return blog, _set_visitor(gate, visitor, created, request), principal, visitor
        return blog, None, principal, visitor

    @app.post("/blog/{blog_slug}/unlock", include_in_schema=False)
    def unlock_blog(request: Request, blog_slug: str, payload: dict = Body(...)):
        blog = service.public_blog(blog_slug)
        if not blog:
            raise ToolboxError("BLOG_NOT_FOUND", "博客不存在", status_code=404)
        visitor, created = _visitor(request)
        result = service.unlock_blog(blog, str(payload.get("password") or ""), visitor)
        return _set_visitor(JSONResponse({"authenticated": True, **result}), visitor, created, request)

    @app.post("/blog/{blog_slug}/logout", include_in_schema=False)
    def logout_blog(request: Request, blog_slug: str):
        blog = service.public_blog(blog_slug)
        if not blog:
            raise ToolboxError("BLOG_NOT_FOUND", "博客不存在", status_code=404)
        visitor, created = _visitor(request)
        service.lock_blog(blog, visitor)
        return _set_visitor(JSONResponse({"authenticated": False}), visitor, created, request)

    @app.post("/blog/{blog_slug}/shares", include_in_schema=False)
    def create_share(request: Request, blog_slug: str, payload: dict = Body(...)):
        visitor, created = _visitor(request)
        item = service.create_share(blog_slug, str(payload.get("articleSlug") or ""), payload, get_optional_user(request), visitor)
        return _set_visitor(JSONResponse({"share": item}), visitor, created, request)

    @app.get("/blog/{blog_slug}/share-access", include_in_schema=False)
    def share_access(request: Request, blog_slug: str):
        blog = service.public_blog(blog_slug)
        if not blog or not blog["shareEnabled"]:
            raise ToolboxError("SHARING_DISABLED", "该博客尚未开启分享功能", status_code=403)
        visitor, created = _visitor(request)
        principal = service.blog_access(blog, get_optional_user(request), visitor)
        response = JSONResponse({"canShare": bool(principal["canShare"])})
        return _set_visitor(response, visitor, created, request)

    @app.post("/blog/share/{share_token}/unlock", include_in_schema=False)
    def unlock_share(request: Request, share_token: str, payload: dict = Body(...)):
        visitor, created = _visitor(request)
        service.unlock_share(share_token, str(payload.get("password") or ""), visitor)
        return _set_visitor(JSONResponse({"authenticated": True}), visitor, created, request)

    @app.get("/blog/{blog_slug}", include_in_schema=False)
    def blog_home(request: Request, blog_slug: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        response = _list(request,blog,principal=principal); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/directory", include_in_schema=False)
    def directory(request: Request, blog_slug: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        selected=request.query_params.get("path", "").strip().strip("/")
        response = _layout(request, blog, "目录", _directory_body(blog, selected, _collapsed_directories(request)), principal=principal); service.record_access(blog, request, status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/tags", include_in_schema=False)
    def tags(request: Request, blog_slug: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        base=f'/blog/{quote(blog_slug)}'; tags = service.public_tag_counts(blog["id"])
        body='<section class="blog-tags"><h2>标签</h2><div>'+''.join(f'<a href="{base}/tags/{quote(item["tag"])}">#{_esc(item["tag"])} <small>{item["count"]}</small></a>' for item in tags)+'</div></section>'
        response = _layout(request, blog, "标签", body, principal=principal); service.record_access(blog, request, status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/archive", include_in_schema=False)
    def archive(request: Request, blog_slug: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        response = _list(request,blog,archive=True,principal=principal); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/tags/{tag}", include_in_schema=False)
    def tag(request: Request, blog_slug: str, tag: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        response = _list(request,blog,tag=tag,principal=principal); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/posts/{article_slug:path}", include_in_schema=False)
    def article(request: Request, blog_slug: str, article_slug: str):
        blog, denied, principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return denied
        item=service.public_article(blog["id"],article_slug)
        if not item:
            response = _missing(request,blog_slug); service.record_access(blog,request,status_code=404,article_slug=article_slug)
            return response
        article_body = _article_body(blog, item, share_button=_share_control(blog, item, principal))
        response = _layout(request,blog,item["title"],article_body,description=item["summary"],article_theme=True,principal=principal)
        service.record_access(blog,request,status_code=response.status_code,article_slug=article_slug)
        return response

    @app.get("/blog/{blog_slug}/assets/{asset_path:path}", include_in_schema=False)
    def asset(request: Request, blog_slug: str, asset_path: str):
        blog, denied, _principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return Response(status_code=404)
        path=service.public_asset(blog,asset_path) if blog else None
        response = FileResponse(path) if path else Response(status_code=404)
        if blog: service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/theme/{asset_path:path}", include_in_schema=False)
    def theme_asset(request: Request, blog_slug: str, asset_path: str):
        blog, denied, _principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return Response(status_code=404)
        if not blog or not _site(blog).get("customTemplate"): return Response(status_code=404)
        path=(service.template_dir(blog["id"]) / asset_path).resolve(); root=service.template_dir(blog["id"]).resolve()
        return FileResponse(path) if path.is_file() and root in path.parents else Response(status_code=404)

    @app.get("/blog/{blog_slug}/custom-theme.css", include_in_schema=False)
    def custom_theme(request: Request, blog_slug: str):
        blog, denied, _principal, _visitor_token = protected_blog(request, blog_slug)
        if denied: return Response(status_code=404)
        raw=service.public_theme_css(blog) if blog else None
        return Response(raw,media_type="text/css",headers={"X-Content-Type-Options":"nosniff"}) if raw is not None else Response(status_code=404)

    @app.get("/blog/{blog_slug}/feed.xml", include_in_schema=False)
    def rss(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog or blog["visibility"] == "private": return _missing(request)
        articles,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"; site=_site(blog)
        entries=''.join(f'<item><title>{_esc(a["title"])}</title><link>{_esc(_url(request,base+"/posts/"+quote(a["slug"])))}</link><description>{_esc(a["summary"])}</description><pubDate>{_esc(a["publishedAt"])}</pubDate></item>' for a in articles)
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{_esc(site["title"])}</title><link>{_esc(_url(request,base))}</link><description>{_esc(site["description"])}</description>{entries}</channel></rss>',media_type="application/rss+xml")

    @app.get("/blog/{blog_slug}/atom.xml", include_in_schema=False)
    def atom(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog or blog["visibility"] == "private": return _missing(request)
        articles,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"; site=_site(blog)
        entries=''.join(f'<entry><title>{_esc(a["title"])}</title><id>{_esc(_url(request,base+"/posts/"+quote(a["slug"])))}</id><link href="{_esc(_url(request,base+"/posts/"+quote(a["slug"]))) }"/><updated>{_esc(a["updatedAt"])}</updated><summary>{_esc(a["summary"])}</summary></entry>' for a in articles)
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>{_esc(site["title"])}</title><id>{_esc(_url(request,base))}</id><updated>{_esc(blog.get("updated_at") or blog["created_at"])}</updated>{entries}</feed>',media_type="application/atom+xml")

    @app.get("/blog/{blog_slug}/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog or blog["visibility"] == "private": return _missing(request)
        items,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"
        urls=[_url(request,base),*[_url(request,base+"/posts/"+quote(a["slug"])) for a in items]]
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>{_esc(u)}</loc></url>' for u in urls)+'</urlset>',media_type="application/xml")

    @app.get("/blog/share/{share_token}", include_in_schema=False)
    def shared_article(request: Request, share_token: str):
        visitor, created = _visitor(request)
        try:
            share, blog, item = service.open_share(share_token, get_optional_user(request), visitor)
        except ToolboxError as exc:
            if exc.code == "SHARE_PASSWORD_REQUIRED":
                response = _gate_page(request, title="此分享受密码保护", message="请输入分享密码后查看文档。", share_token=share_token)
            elif exc.code == "BLOG_LOGIN_REQUIRED":
                try:
                    _row, private_blog = service._share_row(share_token)
                except ToolboxError:
                    private_blog = None
                response = _gate_page(request, title="此分享来自私密博客", message="请使用受邀平台账号或博客访问密码进入。", blog_slug=private_blog["slug"] if private_blog else "", status_code=401)
            else:
                response = HTMLResponse(f'<!doctype html><meta name="robots" content="noindex"><main style="font-family:system-ui;text-align:center;padding:5rem"><h1>分享不可用</h1><p>{_esc(exc.message)}</p></main>', status_code=exc.status_code)
            return _set_visitor(response, visitor, created, request)
        if share["mode"] == "document":
            original = f'/blog/{quote(blog["slug"])}/assets/'
            item = {**item, "html": item["html"].replace(original, f'/blog/share/{quote(share_token)}/assets/')}
            body = _article_body(blog, item)
            response = _layout(request, blog, item["title"], body, description=item["summary"], article_theme=True, include_nav=False, noindex=True, resource_base=f'/blog/share/{quote(share_token)}')
        else:
            response = _layout(request, blog, item["title"], _article_body(blog, item), description=item["summary"], article_theme=True, noindex=True)
        service.record_access(blog, request, status_code=response.status_code, article_slug=item["slug"])
        return _set_visitor(response, visitor, created, request)

    @app.get("/blog/share/{share_token}/assets/{asset_path:path}", include_in_schema=False)
    def shared_asset(request: Request, share_token: str, asset_path: str):
        visitor, created = _visitor(request)
        try:
            _share, blog, _item = service.open_share(share_token, get_optional_user(request), visitor, count_view=False)
        except ToolboxError:
            return Response(status_code=404)
        path = service.public_asset(blog, asset_path)
        response = FileResponse(path) if path else Response(status_code=404)
        return _set_visitor(response, visitor, created, request)

    @app.get("/blog/share/{share_token}/custom-theme.css", include_in_schema=False)
    def shared_custom_theme(request: Request, share_token: str):
        visitor, created = _visitor(request)
        try:
            _share, blog, _item = service.open_share(share_token, get_optional_user(request), visitor, count_view=False)
        except ToolboxError:
            return Response(status_code=404)
        raw = service.public_theme_css(blog)
        response = Response(raw, media_type="text/css", headers={"X-Content-Type-Options":"nosniff"}) if raw is not None else Response(status_code=404)
        return _set_visitor(response, visitor, created, request)

    @app.get("/blog/share/{share_token}/theme/{asset_path:path}", include_in_schema=False)
    def shared_template_asset(request: Request, share_token: str, asset_path: str):
        visitor, created = _visitor(request)
        try:
            _share, blog, _item = service.open_share(share_token, get_optional_user(request), visitor, count_view=False)
        except ToolboxError:
            return Response(status_code=404)
        path=(service.template_dir(blog["id"]) / asset_path).resolve(); root=service.template_dir(blog["id"]).resolve()
        response = FileResponse(path) if path.is_file() and root in path.parents else Response(status_code=404)
        return _set_visitor(response, visitor, created, request)
