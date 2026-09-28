from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    assert 'class="git-blog-page theme-dark"' in listing
    assert '<link rel="stylesheet" href="/blog/notes/custom-theme.css">' in article
    assert 'class="git-blog-page theme-custom"' in article


def test_outline_script_targets_the_stable_typora_article_container() -> None:
    script = (Path(__file__).parents[1] / "assets" / "public.js").read_text(encoding="utf-8")

    assert "articleLayout?.querySelector('#write')" in script
    assert "articleLayout?.querySelector('.markdown-body')" not in script
