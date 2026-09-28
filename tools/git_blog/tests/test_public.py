from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
