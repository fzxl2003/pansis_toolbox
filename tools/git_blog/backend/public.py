from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from tools.git_blog.backend import service

ASSET_VERSION = "custom-theme-outline-20260928"


def _site(blog: dict) -> dict:
    configured_site = blog["config"]["site"]
    config = {
        **(blog.get("effectiveConfig") or blog["config"])["site"],
        "customTemplate": configured_site.get("customTemplate", False),
        "customThemeId": configured_site.get("customThemeId", ""),
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


def _layout(request: Request, blog: dict, title: str, body: str, *, description: str = "", not_found: bool = False, article_theme: bool = False) -> HTMLResponse:
    site = _site(blog); base = f"/blog/{quote(blog['slug'])}"; css = "/tool-assets/git_blog"
    template_css = f'<link rel="stylesheet" href="{base}/theme/style.css">' if site.get("customTemplate") else ""
    typora_css = f'<link rel="stylesheet" href="{base}/custom-theme.css">' if article_theme and site.get("customThemeId") else ""
    custom = template_css + typora_css
    body_theme = "custom" if article_theme and site.get("customThemeId") else str(site.get("theme", "auto"))
    head = f'''<!doctype html><html lang="{_esc(site.get('language','zh-CN'))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)} · {_esc(site['title'])}</title><meta name="description" content="{_esc(description or site['description'])}"><link rel="canonical" href="{_esc(_url(request, request.url.path))}"><meta property="og:title" content="{_esc(title)}"><meta property="og:description" content="{_esc(description or site['description'])}"><link rel="stylesheet" href="{css}/vendor/github-markdown.css"><link rel="stylesheet" href="{css}/vendor/katex.min.css"><link rel="stylesheet" href="{css}/vendor/highlight.css"><link rel="stylesheet" href="{css}/blog.css?v={ASSET_VERSION}">{custom}<style>:root{{--blog-user-accent:{_esc(site.get('accentColor','#42b983'))};--blog-width:{int(site.get('contentWidth',860))}px;--blog-font:{_esc(site.get('fontFamily','Ubuntu, Source Sans Pro, sans-serif'))};}}</style></head>'''
    icons=f'<a class="blog-icon" title="RSS" href="{base}/feed.xml">◔</a><a class="blog-icon" title="Atom" href="{base}/atom.xml">◉</a>'
    nav=f'<header class="blog-head"><a href="{base}" class="blog-brand">{_esc(site["title"])}</a><nav class="blog-nav"><a href="{base}">首页</a><a href="{base}/directory">目录</a><a href="{base}/tags">标签</a></nav><span class="blog-subscribe">{icons}</span></header>'
    return HTMLResponse(head+f'<body class="git-blog-page theme-{_esc(body_theme)}"><main class="blog-shell">{nav}{body}</main><script type="module" src="{css}/public.js?v={ASSET_VERSION}"></script></body></html>', status_code=404 if not_found else 200)


def _missing(request: Request, slug: str = "") -> HTMLResponse:
    if slug and (blog := service.public_blog(slug)):
        return _layout(request,blog,"页面不存在",'<section class="blog-not-found"><h2>404</h2><p>请求的内容不存在或尚未发布。</p></section>',not_found=True)
    return HTMLResponse('<!doctype html><title>博客不存在</title><main style="font-family:system-ui;text-align:center;padding:5rem"><h1>404</h1><p>博客不存在或暂不可访问。</p></main>',status_code=404)


def _list(request: Request, blog: dict, *, tag: str = "", archive: bool = False) -> HTMLResponse:
    page=max(1,int(request.query_params.get("page","1") or 1)); query=request.query_params.get("q","").strip()[:100]
    articles,total=service.public_articles(blog["id"],page=page,tag=tag,query=query)
    base=f"/blog/{quote(blog['slug'])}"; items=[]
    for article in articles:
        items.append(_article_card(article, base))
    title = f"标签：{tag}" if tag else ("归档" if archive else "")
    search=f'<form class="blog-search" action="{base}" method="get"><input name="q" value="{_esc(query)}" placeholder="搜索文章"><button>搜索</button></form>' if not archive else ""
    tags=' '.join(f'<a href="{base}/tags/{quote(t)}">#{_esc(t)}</a>' for t in service.public_tags(blog["id"])) if tag else ''
    heading=f'<h2>{_esc(title)}</h2>' if title else ''
    body=f'{heading}{search}<p class="blog-meta">{tags}</p><ul class="blog-list">{"".join(items) or "<li>暂无已发布文章。</li>"}</ul>{_pagination(request,page,total)}'
    return _layout(request,blog,title,body,description=_site(blog)["description"])


def _directory_body(blog: dict, selected: str = "") -> str:
    base = f'/blog/{quote(blog["slug"])}'
    def node(item: dict, parent: str = "") -> str:
        path = f'{parent}/{item["name"]}'.strip('/')
        children = ''.join(node(child, path) for child in item["children"] if child["children"])
        if not item["children"]:
            return ""
        link = f'<a href="{base}/directory?path={quote(path)}">{_esc(item["name"])}</a>'
        return f'<li><details open><summary>{link}</summary><ul>{children}</ul></details></li>' if children else f'<li class="blog-folder-leaf">{link}</li>'
    articles,_ = service.public_articles(blog["id"], path_prefix=selected)
    cards=''.join(_article_card(article, base, show_meta=False) for article in articles)
    return f'<section class="blog-directory-layout"><aside class="blog-directory"><h2>目录</h2><ul>{"".join(node(item) for item in service.public_directory(blog["id"])["children"])}</ul></aside><section><h2>{_esc(selected or "全部文档")}</h2><ul class="blog-list">{cards or "<li>暂无文档。</li>"}</ul></section></section>'


def mount_extra(app: FastAPI) -> None:
    @app.get("/blog", include_in_schema=False)
    def blog_about() -> HTMLResponse:
        return HTMLResponse('<!doctype html><title>Git 博客</title><main style="font-family:system-ui;max-width:720px;margin:4rem auto;padding:0 1rem"><h1>Git 博客</h1><p>这是由 Pansis Toolbox 托管的 Markdown 博客服务。</p></main>')

    @app.get("/blog/{blog_slug}", include_in_schema=False)
    def blog_home(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        response = _list(request,blog); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/directory", include_in_schema=False)
    def directory(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        selected=request.query_params.get("path", "").strip().strip("/")
        response = _layout(request, blog, "目录", _directory_body(blog, selected)); service.record_access(blog, request, status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/tags", include_in_schema=False)
    def tags(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        base=f'/blog/{quote(blog_slug)}'; tags = service.public_tag_counts(blog["id"])
        body='<section class="blog-tags"><h2>标签</h2><div>'+''.join(f'<a href="{base}/tags/{quote(item["tag"])}">#{_esc(item["tag"])} <small>{item["count"]}</small></a>' for item in tags)+'</div></section>'
        response = _layout(request, blog, "标签", body); service.record_access(blog, request, status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/archive", include_in_schema=False)
    def archive(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        response = _list(request,blog,archive=True); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/tags/{tag}", include_in_schema=False)
    def tag(request: Request, blog_slug: str, tag: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        response = _list(request,blog,tag=tag); service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/posts/{article_slug:path}", include_in_schema=False)
    def article(request: Request, blog_slug: str, article_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        item=service.public_article(blog["id"],article_slug)
        if not item:
            response = _missing(request,blog_slug); service.record_access(blog,request,status_code=404,article_slug=article_slug)
            return response
        article_class = "typora-export" if _site(blog).get("customThemeId") else "markdown-body typora-export"
        article_body = f'<section class="blog-article-layout"><button class="blog-outline-toggle" type="button" aria-expanded="true" aria-controls="blog-outline">大纲</button><aside class="blog-outline" id="blog-outline" aria-label="文章大纲"><div class="blog-outline-head"><strong>目录</strong><button type="button" class="blog-outline-close" aria-label="隐藏大纲">×</button></div><nav class="blog-outline-list"></nav></aside><article id="write" class="{article_class}">{item["html"]}</article></section>'
        response = _layout(request,blog,item["title"],article_body,description=item["summary"],article_theme=True)
        service.record_access(blog,request,status_code=response.status_code,article_slug=article_slug)
        return response

    @app.get("/blog/{blog_slug}/assets/{asset_path:path}", include_in_schema=False)
    def asset(request: Request, blog_slug: str, asset_path: str):
        blog=service.public_blog(blog_slug)
        path=service.public_asset(blog,asset_path) if blog else None
        response = FileResponse(path) if path else Response(status_code=404)
        if blog: service.record_access(blog,request,status_code=response.status_code)
        return response

    @app.get("/blog/{blog_slug}/theme/{asset_path:path}", include_in_schema=False)
    def theme_asset(blog_slug: str, asset_path: str):
        blog=service.public_blog(blog_slug)
        if not blog or not _site(blog).get("customTemplate"): return Response(status_code=404)
        path=(service.template_dir(blog["id"]) / asset_path).resolve(); root=service.template_dir(blog["id"]).resolve()
        return FileResponse(path) if path.is_file() and root in path.parents else Response(status_code=404)

    @app.get("/blog/{blog_slug}/custom-theme.css", include_in_schema=False)
    def custom_theme(blog_slug: str):
        blog=service.public_blog(blog_slug)
        raw=service.public_theme_css(blog) if blog else None
        return Response(raw,media_type="text/css",headers={"X-Content-Type-Options":"nosniff"}) if raw is not None else Response(status_code=404)

    @app.get("/blog/{blog_slug}/feed.xml", include_in_schema=False)
    def rss(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        articles,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"; site=_site(blog)
        entries=''.join(f'<item><title>{_esc(a["title"])}</title><link>{_esc(_url(request,base+"/posts/"+quote(a["slug"])))}</link><description>{_esc(a["summary"])}</description><pubDate>{_esc(a["publishedAt"])}</pubDate></item>' for a in articles)
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{_esc(site["title"])}</title><link>{_esc(_url(request,base))}</link><description>{_esc(site["description"])}</description>{entries}</channel></rss>',media_type="application/rss+xml")

    @app.get("/blog/{blog_slug}/atom.xml", include_in_schema=False)
    def atom(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        articles,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"; site=_site(blog)
        entries=''.join(f'<entry><title>{_esc(a["title"])}</title><id>{_esc(_url(request,base+"/posts/"+quote(a["slug"])))}</id><link href="{_esc(_url(request,base+"/posts/"+quote(a["slug"]))) }"/><updated>{_esc(a["updatedAt"])}</updated><summary>{_esc(a["summary"])}</summary></entry>' for a in articles)
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>{_esc(site["title"])}</title><id>{_esc(_url(request,base))}</id><updated>{_esc(blog.get("updated_at") or blog["created_at"])}</updated>{entries}</feed>',media_type="application/atom+xml")

    @app.get("/blog/{blog_slug}/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request, blog_slug: str):
        blog=service.public_blog(blog_slug)
        if not blog: return _missing(request)
        items,_=service.public_articles(blog["id"]); base=f"/blog/{quote(blog_slug)}"
        urls=[_url(request,base),*[_url(request,base+"/posts/"+quote(a["slug"])) for a in items]]
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>{_esc(u)}</loc></url>' for u in urls)+'</urlset>',media_type="application/xml")
