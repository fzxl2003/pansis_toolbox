from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from starlette.requests import Request

from tools.git_blog.backend import public


def _request(query: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/blog/notes", "query_string": query, "headers": []})


def test_relative_time_uses_human_readable_ranges() -> None:
    now = datetime.now(timezone.utc)
    assert public._relative_time((now - timedelta(seconds=15)).isoformat()) == "刚刚"
    assert public._relative_time((now - timedelta(minutes=8)).isoformat()) == "8 分钟前"
    assert public._relative_time((now - timedelta(hours=3)).isoformat()) == "3 小时前"
    assert public._relative_time((now - timedelta(days=4)).isoformat()) == "4 天前"
    assert public._relative_time((now - timedelta(days=8)).isoformat()) == (now - timedelta(days=8)).strftime("%Y-%m-%d")


def test_article_card_is_a_keyboard_accessible_click_target() -> None:
    card = public._article_card(
        {"slug": "guide/intro", "title": "Intro", "tags": ["guide"], "coverPath": "", "publishedAt": "2026-09-11T21:57:59+08:00", "summary": "Summary"},
        "/blog/notes",
    )
    assert 'data-href="/blog/notes/posts/guide/intro"' in card
    assert 'tabindex="0"' in card
    assert 'class="blog-time"' in card


def test_pagination_preserves_search_and_includes_page_numbers() -> None:
    pagination = public._pagination(_request(b"q=hello&page=4"), 4, 500)
    assert 'aria-current="page">4</a>' in pagination
    assert '?q=hello&amp;page=3' in pagination
    assert '>1</a>' in pagination
    assert '>10</a>' in pagination
    assert 'blog-page-ellipsis' in pagination


def test_custom_typora_theme_is_only_loaded_for_article_layout() -> None:
    blog = {
        "id": "blog-1",
        "slug": "notes",
        "name": "Notes",
        "config": {"site": {"theme": "dark", "customThemeId": "theme-1", "customTemplate": False}},
        "effectiveConfig": {"site": {"theme": "dark", "language": "zh-CN"}},
    }

    listing = public._layout(_request(), blog, "首页", "<p>list</p>").body.decode()
    article = public._layout(_request(), blog, "文章", '<article id="write"></article>', article_theme=True).body.decode()

    assert "/blog/notes/custom-theme.css" not in listing
    assert 'class="git-blog-page theme-dark blog-overview-page"' in listing
    assert '<link rel="stylesheet" href="/blog/notes/custom-theme.css">' in article
    assert 'class="git-blog-page theme-custom blog-article-page"' in article


def test_header_is_sticky_only_on_non_article_pages() -> None:
    css = (Path(__file__).parents[1] / "assets" / "blog.css").read_text(encoding="utf-8")

    assert ".blog-overview-page .blog-head { position: sticky;" in css
    assert ".blog-article-page .blog-head { position: sticky;" not in css


def test_layout_applies_configured_accent_color_and_keeps_green_as_default() -> None:
    blog = {
        "id": "blog-1",
        "slug": "notes",
        "name": "Notes",
        "config": {"site": {"theme": "auto", "accentColor": "#7c3aed", "accentColorEnabled": True}},
        "effectiveConfig": {"site": {"theme": "auto", "language": "zh-CN"}},
    }

    configured = public._layout(_request(), blog, "首页", "<p>list</p>").body.decode()
    blog["config"]["site"]["accentColorEnabled"] = False
    defaulted = public._layout(_request(), blog, "首页", "<p>list</p>").body.decode()

    assert "--blog-user-accent:#7c3aed" in configured
    assert "--blog-user-accent:#42b983" in defaulted


def test_outline_script_targets_the_stable_typora_article_container() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "articleLayout?.querySelector('#write')" in script
    assert "articleLayout?.querySelector('.markdown-body')" not in script


def test_outline_is_responsive_and_mobile_navigation_closes_it() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "setOutline(!narrowViewport.matches)" in script
    assert "if (narrowViewport.matches) setOutline(false)" in script
    assert "outlineBackdrop?.addEventListener('click', () => setOutline(false))" in script
    assert "narrowViewport.addEventListener('change'" in script


def test_outline_controls_use_menu_and_collapse_icons() -> None:
    source = (Path(__file__).parents[1] / "backend" / "public.py").read_text(encoding="utf-8")

    assert 'aria-label="显示目录"' in source
    assert 'd="M4 6h16M4 12h16M4 18h16"' in source
    assert 'aria-label="收起目录"' in source
    assert 'd="m15 18-6-6 6-6"' in source
    assert 'class="blog-outline-rail"' in source
    assert 'class="blog-outline-backdrop"' in source
    assert 'class="blog-back-to-top"' in public._back_to_top_button()
    assert '>大纲</button>' not in source


def test_article_back_to_top_appears_after_scrolling_and_honors_reduced_motion() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "window.scrollY > 320" in script
    assert "window.scrollTo({ top: 0" in script
    assert "prefers-reduced-motion: reduce" in script


def test_back_to_top_button_is_reusable_for_listing_pages() -> None:
    button = public._back_to_top_button("blog-home-back-to-top")

    assert 'class="blog-back-to-top blog-home-back-to-top"' in button
    assert 'aria-label="返回页面开头"' in button


def test_directory_cookie_parses_collapsed_paths_and_rejects_invalid_values() -> None:
    encoded_paths = quote('["docs"]')
    valid = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/blog/notes/directory", "query_string": b"", "headers": [(b"cookie", f"{public.DIRECTORY_COOKIE_NAME}={encoded_paths}".encode())]})
    invalid = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/blog/notes/directory", "query_string": b"", "headers": [(b"cookie", f"{public.DIRECTORY_COOKIE_NAME}=not-json".encode())]})

    assert public._collapsed_directories(valid) == {"docs"}
    assert public._collapsed_directories(invalid) == set()


def test_directory_body_uses_saved_folder_state(monkeypatch) -> None:
    monkeypatch.setattr(public.service, "public_directory", lambda _blog_id: {"children": [{"name": "docs", "children": [{"name": "guide", "children": [{"name": "intro.md", "children": []}]}]}]})
    monkeypatch.setattr(public.service, "public_articles", lambda _blog_id, **_kwargs: ([], 0))
    blog = {"id": "blog-1", "slug": "notes"}

    expanded = public._directory_body(blog)
    collapsed = public._directory_body(blog, collapsed={"docs"})

    assert '<details data-directory-path="docs" open>' in expanded
    assert '<details data-directory-path="docs">' in collapsed
    assert 'data-cookie-path="/blog/notes"' in collapsed
    assert 'class="blog-directory-rail"' in collapsed
    assert 'class="blog-directory-toggle"' in collapsed
    assert 'class="blog-directory-backdrop"' in collapsed
    assert 'class="blog-back-to-top"' in collapsed


def test_directory_panel_uses_responsive_sidebar_controls() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "setDirectoryVisible(!narrowDirectoryViewport.matches)" in script
    assert "directoryBackdrop?.addEventListener('click', () => setDirectoryVisible(false))" in script
    assert "if (narrowDirectoryViewport.matches) setDirectoryVisible(false)" in script


def test_directory_script_saves_collapsed_paths_in_a_scoped_cookie() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "details.filter((item) => !item.open)" in script
    assert "git_blog_directory_collapsed=" in script
    assert "Path=${blogDirectory.dataset.cookiePath}" in script
    assert "SameSite=Lax" in script
