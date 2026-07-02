from pathlib import Path

from starlette.requests import Request

from tools.web_proxy.backend import router as web_proxy_router


def test_normalize_target_url_adds_https() -> None:
    assert web_proxy_router._normalize_target_url("example.com") == "https://example.com"


def test_normalize_target_url_keeps_http_scheme() -> None:
    assert web_proxy_router._normalize_target_url("http://example.com/a") == "http://example.com/a"


def test_session_meta_round_trip(tmp_path: Path) -> None:
    web_proxy_router._write_session_id(tmp_path, "abc123")
    assert web_proxy_router._read_session_id(tmp_path) == "abc123"


def test_external_origin_keeps_forwarded_host() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/web-proxy",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-host", b"toolbox.example.com"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    assert web_proxy_router._external_origin(request) == "https://toolbox.example.com"
