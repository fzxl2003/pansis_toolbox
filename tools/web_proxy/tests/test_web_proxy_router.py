import asyncio
import json

import pytest
from starlette.requests import Request

from tools.web_proxy.backend import router as web_proxy_router
from tools.web_proxy.backend import service as web_proxy_service

VALID_SESSION_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


# ---------------------------------------------------------------------------
# Existing helper tests
# ---------------------------------------------------------------------------


def test_normalize_target_url_adds_https() -> None:
    assert web_proxy_router._normalize_target_url("example.com") == "https://example.com"


def test_normalize_target_url_keeps_http_scheme() -> None:
    assert web_proxy_router._normalize_target_url("http://example.com/a") == "http://example.com/a"


def test_session_exit_selection_round_trip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    web_proxy_service._initialized_dbs.clear()
    web_proxy_service.save_session("user-a", "abc123", "global-server-id")
    assert web_proxy_service.get_session("user-a")["exitServerId"] == "global-server-id"
    assert web_proxy_service.get_session("user-b") is None
    web_proxy_service.set_session_exit("user-a", None)
    assert web_proxy_service.get_session("user-a")["exitServerId"] is None


def test_test_sites_are_deduplicated_and_user_isolated(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    web_proxy_service._initialized_dbs.clear()
    first = web_proxy_service.add_test_site("user-a", "https://example.com/")
    duplicate = web_proxy_service.add_test_site("user-a", "https://example.com/")
    web_proxy_service.add_test_site("user-b", "https://example.org/")
    assert duplicate["id"] == first["id"]
    assert [site["url"] for site in web_proxy_service.list_test_sites("user-a")] == ["https://example.com/"]
    assert [site["url"] for site in web_proxy_service.list_test_sites("user-b")] == ["https://example.org/"]
    assert web_proxy_service.delete_test_site("user-a", first["id"]) is True
    assert web_proxy_service.list_test_sites("user-a") == []


def test_normalize_test_site_url_strips_fragment() -> None:
    assert web_proxy_router._normalize_test_site_url("example.com/status#section") == "https://example.com/status"
    with pytest.raises(Exception):
        web_proxy_router._normalize_test_site_url("https:///missing-host")


def test_probe_site_reports_http_status_from_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 204

        def getcode(self):
            return 204

        def geturl(self):
            return "https://example.com/health"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.com/health"
            assert timeout == 15
            return FakeResponse()

    monkeypatch.setattr(web_proxy_router, "build_opener", lambda *_handlers: FakeOpener())
    result = web_proxy_router._probe_site_through_tunnel("http://127.0.0.1:19999", "https://example.com/health")
    assert result["reachable"] is True
    assert result["statusCode"] == 204
    assert result["error"] is None


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


def test_forwarded_public_headers_are_added_for_direct_backend_request() -> None:
    scope = {
        "type": "http", "method": "GET", "path": "/abc", "query_string": b"",
        "headers": [(b"host", b"toolbox.example.com:8000")], "scheme": "http",
        "server": ("127.0.0.1", 8000),
    }
    assert web_proxy_router._forwarded_public_headers(scope) == [
        ("X-Forwarded-Proto", "http"), ("X-Forwarded-Host", "toolbox.example.com:8000"),
    ]


# ---------------------------------------------------------------------------
# _is_rammerhead_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    f"/{VALID_SESSION_ID}",
    f"/{VALID_SESSION_ID}/",
    f"/{VALID_SESSION_ID}/https://example.com",
    f"/{VALID_SESSION_ID}/https://example.com/path?q=1",
    f"/{VALID_SESSION_ID}!s!utf-8/https://s1.hdslb.com/bfs/static/app.js",
    f"/{VALID_SESSION_ID}*popup-window/https://www.bilibili.com/video/BV1xx",
    "/hammerhead.js",
    "/rammerhead.js",
    "/worker-hammerhead.js",
    "/transport-worker.js",
    "/task.js",
    "/iframe-task.js",
    "/messaging",
    "/syncLocalStorage",
    "/api/shuffleDict",
])
def test_is_rammerhead_path_detects_rammerhead_routes(path: str) -> None:
    assert web_proxy_router._is_rammerhead_path(path) is True


@pytest.mark.parametrize("path", [
    "/",
    "/login",
    "/settings",
    "/tools/web_proxy",
    "/api/health",
    "/api/tools",
    "/api/tools/web-proxy",
    "/web-proxy",
    "/web-proxy?url=https://example.com",
    "/assets/main-Bxk7f.js",
    "/tool-assets/web_proxy/logo.png",
    "/docker-manager",
    "/ssh-workspace/ws/terminal",
    f"/{'a' * 31}",          # 31 chars – too short
    f"/{'a' * 33}",          # 33 chars – too long
    f"/{'g' * 32}",          # 32 chars but not hex
    f"/{VALID_SESSION_ID}suffix",  # not a Rammerhead session separator
    "/hammerhead.js.bak",    # similar but not exact
    "/api/shuffleDict/extra",
])
def test_is_rammerhead_path_rejects_non_rammerhead_routes(path: str) -> None:
    assert web_proxy_router._is_rammerhead_path(path) is False


# ---------------------------------------------------------------------------
# _apply_mask (WebSocket XOR masking)
# ---------------------------------------------------------------------------


def test_apply_mask_round_trip() -> None:
    mask = b"\x12\x34\x56\x78"
    payload = b"Hello, WebSocket!"
    masked = web_proxy_router._apply_mask(payload, mask)
    # Masking is its own inverse.
    assert web_proxy_router._apply_mask(masked, mask) == payload


def test_apply_mask_empty_payload() -> None:
    assert web_proxy_router._apply_mask(b"", b"\x00\x00\x00\x00") == b""


def test_apply_mask_known_value() -> None:
    # Manually verified: mask [0x37,0x0F,0xA3,0xD4], payload "Hello"
    # H ^ 0x37=0x7F  e ^ 0x0F=0x6A  l ^ 0xA3=0xCF  l ^ 0xD4=0xB8  o ^ 0x37=0x58
    mask = bytes([0x37, 0x0F, 0xA3, 0xD4])
    payload = b"Hello"
    masked = web_proxy_router._apply_mask(payload, mask)
    assert masked == bytes([0x7F, 0x6A, 0xCF, 0xB8, 0x58])


# ---------------------------------------------------------------------------
# WebSocket frame helpers
# ---------------------------------------------------------------------------


def test_recv_ws_frame_unmasked_text() -> None:
    """_recv_ws_frame reads an unmasked text frame (server→client)."""

    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"\x81\x05Hello")  # FIN+text, len=5, no mask
        reader.feed_eof()
        opcode, payload = await web_proxy_router._recv_ws_frame(reader)
        assert opcode == 0x1
        assert payload == b"Hello"

    asyncio.run(run())


def test_recv_ws_frame_masked_binary() -> None:
    """_recv_ws_frame reads a masked binary frame (client→server)."""

    async def run() -> None:
        mask = b"\x01\x02\x03\x04"
        payload = b"\x00\x01\x02\x03"  # after unmasking
        masked = web_proxy_router._apply_mask(payload, mask)
        frame = bytes([0x82, 0x84]) + mask + masked  # FIN+binary, MASK+len=4
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        opcode, received = await web_proxy_router._recv_ws_frame(reader)
        assert opcode == 0x2
        assert received == payload

    asyncio.run(run())


class _MockWriter:
    """Minimal asyncio.StreamWriter stand-in for testing _send_ws_frame."""

    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass


def test_send_ws_frame_masks_payload() -> None:
    """_send_ws_frame produces a correctly masked client→server frame."""

    async def run() -> None:
        writer = _MockWriter()
        await web_proxy_router._send_ws_frame(writer, 0x1, b"Hello")
        data = bytes(writer.data)
        assert data[0] == 0x81              # FIN + text opcode
        assert data[1] == 0x85              # MASK bit + length 5
        mask = data[2:6]
        masked_payload = data[6:]
        assert len(masked_payload) == 5
        assert web_proxy_router._apply_mask(masked_payload, mask) == b"Hello"

    asyncio.run(run())


def test_ws_frame_send_then_recv_round_trip() -> None:
    """A frame written by _send_ws_frame is readable by _recv_ws_frame."""

    async def run() -> None:
        writer = _MockWriter()
        await web_proxy_router._send_ws_frame(writer, 0x2, b"binary round trip")
        reader = asyncio.StreamReader()
        reader.feed_data(bytes(writer.data))
        reader.feed_eof()
        opcode, payload = await web_proxy_router._recv_ws_frame(reader)
        assert opcode == 0x2
        assert payload == b"binary round trip"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Middleware – non-rammerhead paths pass through
# ---------------------------------------------------------------------------


def test_middleware_passes_through_non_rammerhead_paths() -> None:
    async def run() -> None:
        called = {"app": False}

        async def dummy_app(scope, receive, send):
            called["app"] = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = web_proxy_router.RammerheadProxyMiddleware(dummy_app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/login",
            "query_string": b"",
            "headers": [(b"host", b"localhost")],
        }
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        await middleware(scope, receive, send)
        assert called["app"] is True
        assert sent[0]["status"] == 200

    asyncio.run(run())


def test_middleware_passes_through_lifespan() -> None:
    """ASGI lifespan events must reach the wrapped app unchanged."""

    async def run() -> None:
        called = {"lifespan": False}

        async def dummy_app(scope, receive, send):
            if scope["type"] == "lifespan":
                called["lifespan"] = True
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})

        middleware = web_proxy_router.RammerheadProxyMiddleware(dummy_app)
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put({"type": "lifespan.startup"})

        async def receive():
            return await queue.get()

        sent: list[dict] = []

        async def send(msg):
            sent.append(msg)

        await middleware({"type": "lifespan"}, receive, send)
        assert called["lifespan"] is True
        assert sent[0]["type"] == "lifespan.startup.complete"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# HTTP proxy – integration test with a mock sidecar
# ---------------------------------------------------------------------------


def test_http_proxy_forwards_request_to_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware proxies an HTTP request to the sidecar and streams the
    response back to the ASGI ``send`` callable."""

    async def run() -> None:
        # --- mock sidecar HTTP server ---
        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            request_line = await reader.readline()
            parts = request_line.decode("ascii").strip().split(" ")
            method, target = parts[0], parts[1]
            hdrs: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.rstrip(b"\r\n").partition(b": ")
                hdrs[k.decode("ascii").lower()] = v.decode("latin-1")
            cl = int(hdrs.get("content-length", "0"))
            body = await reader.readexactly(cl) if cl else b""

            echo = json.dumps(
                {
                    "method": method, "target": target, "host": hdrs.get("host", ""), "body": body.decode("utf-8"),
                    "forwardedProto": hdrs.get("x-forwarded-proto", ""), "forwardedHost": hdrs.get("x-forwarded-host", ""),
                }
            ).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(echo)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            ) + echo
            writer.write(resp)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setattr(web_proxy_router, "SIDECAR_HOST", "127.0.0.1")
        monkeypatch.setattr(web_proxy_router, "SIDECAR_PORT", port)

        async def noop_ensure(scope):
            pass

        monkeypatch.setattr(web_proxy_router, "_ensure_sidecar_for_scope", noop_ensure)

        try:
            middleware = web_proxy_router.RammerheadProxyMiddleware(lambda *a: None)
            scope = {
                "type": "http",
                "method": "GET",
                "path": f"/{VALID_SESSION_ID}/https://example.com",
                "query_string": b"",
                "headers": [(b"host", b"toolbox.example.com"), (b"x-custom", b"val")],
            }
            sent: list[dict] = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(msg):
                sent.append(msg)

            await middleware(scope, receive, send)

            assert sent[0]["status"] == 200
            body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
            data = json.loads(body)
            assert data["method"] == "GET"
            assert data["target"] == f"/{VALID_SESSION_ID}/https://example.com"
            # The Host header must be rewritten to the sidecar, not the toolbox.
            assert data["host"] == f"127.0.0.1:{port}"
            # Direct Uvicorn access has no upstream reverse proxy, so the
            # middleware must provide the public origin itself.
            assert data["forwardedProto"] == "http"
            assert data["forwardedHost"] == "toolbox.example.com"
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_http_proxy_replaces_client_content_length_for_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse proxy must emit exactly one correct length for a POST."""

    async def run() -> None:
        received_lengths: list[str] = []

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = line.rstrip(b"\r\n").partition(b": ")
                if key.lower() == b"content-length":
                    received_lengths.append(value.decode("ascii"))
            length = int(received_lengths[0])
            await reader.readexactly(length)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(web_proxy_router, "SIDECAR_HOST", "127.0.0.1")
        monkeypatch.setattr(web_proxy_router, "SIDECAR_PORT", port)

        async def noop_ensure(scope):
            pass

        monkeypatch.setattr(web_proxy_router, "_ensure_sidecar_for_scope", noop_ensure)
        try:
            middleware = web_proxy_router.RammerheadProxyMiddleware(lambda *a: None)
            payload = b'{"type":"sync","fetch":true}'
            scope = {
                "type": "http", "method": "POST", "path": "/syncLocalStorage", "query_string": b"",
                "headers": [(b"host", b"toolbox.example.com"), (b"content-length", b"999")],
            }
            delivered: list[dict] = []

            async def receive():
                return {"type": "http.request", "body": payload, "more_body": False}

            async def send(message):
                delivered.append(message)

            await middleware(scope, receive, send)
            assert delivered[0]["status"] == 200
            assert received_lengths == [str(len(payload))]
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_http_proxy_returns_502_when_sidecar_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the sidecar TCP server is unreachable the proxy returns 502."""

    async def run() -> None:
        monkeypatch.setattr(web_proxy_router, "SIDECAR_HOST", "127.0.0.1")
        monkeypatch.setattr(web_proxy_router, "SIDECAR_PORT", 1)  # port 1 is almost certainly closed

        async def noop_ensure(scope):
            pass

        monkeypatch.setattr(web_proxy_router, "_ensure_sidecar_for_scope", noop_ensure)

        middleware = web_proxy_router.RammerheadProxyMiddleware(lambda *a: None)
        scope = {
            "type": "http",
            "method": "GET",
            "path": f"/{VALID_SESSION_ID}/https://example.com",
            "query_string": b"",
            "headers": [(b"host", b"toolbox.example.com")],
        }
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        await middleware(scope, receive, send)
        assert sent[0]["status"] == 502

    asyncio.run(run())


def test_http_proxy_forwards_post_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST request bodies are forwarded to the sidecar."""

    async def run() -> None:
        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readline()  # request line
            hdrs: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.rstrip(b"\r\n").partition(b": ")
                hdrs[k.decode("ascii").lower()] = v.decode("latin-1")
            cl = int(hdrs.get("content-length", "0"))
            body = await reader.readexactly(cl) if cl else b""
            resp = b"HTTP/1.1 201 Created\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            writer.write(resp)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(web_proxy_router, "SIDECAR_HOST", "127.0.0.1")
        monkeypatch.setattr(web_proxy_router, "SIDECAR_PORT", port)

        async def noop_ensure(scope):
            pass

        monkeypatch.setattr(web_proxy_router, "_ensure_sidecar_for_scope", noop_ensure)

        try:
            middleware = web_proxy_router.RammerheadProxyMiddleware(lambda *a: None)
            post_body = b'{"hello":"world"}'
            scope = {
                "type": "http",
                "method": "POST",
                "path": f"/{VALID_SESSION_ID}/https://example.com/api",
                "query_string": b"",
                "headers": [(b"host", b"toolbox.example.com"), (b"content-type", b"application/json")],
            }
            sent: list[dict] = []

            async def receive():
                return {"type": "http.request", "body": post_body, "more_body": False}

            async def send(msg):
                sent.append(msg)

            await middleware(scope, receive, send)
            assert sent[0]["status"] == 201
            body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
            assert body == post_body
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Mixed-content prevention – forwarded headers reach the sidecar
# ---------------------------------------------------------------------------


def test_http_proxy_forwards_forwarded_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Forwarded-Proto / X-Forwarded-Host must reach the sidecar so that
    rammerhead's per-request getServerInfo rewrites asset URLs with the
    correct (https) protocol – preventing mixed-content blocks."""

    async def run() -> None:
        received_headers: dict[str, str] = {}

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readline()  # request line
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.rstrip(b"\r\n").partition(b": ")
                received_headers[k.decode("ascii").lower()] = v.decode("latin-1")
            resp = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            writer.write(resp)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(web_proxy_router, "SIDECAR_HOST", "127.0.0.1")
        monkeypatch.setattr(web_proxy_router, "SIDECAR_PORT", port)

        async def noop_ensure(scope):
            pass

        monkeypatch.setattr(web_proxy_router, "_ensure_sidecar_for_scope", noop_ensure)

        try:
            middleware = web_proxy_router.RammerheadProxyMiddleware(lambda *a: None)
            scope = {
                "type": "http",
                "method": "GET",
                "path": f"/{VALID_SESSION_ID}/https://example.com",
                "query_string": b"",
                "headers": [
                    (b"host", b"az.pansis.site:8799"),
                    (b"x-forwarded-proto", b"https"),
                    (b"x-forwarded-host", b"az.pansis.site:8799"),
                ],
            }
            sent: list[dict] = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(msg):
                sent.append(msg)

            await middleware(scope, receive, send)
            # The sidecar must see the real client protocol and host so that
            # getServerInfo(req) rewrites URLs with https://az.pansis.site:8799
            assert received_headers.get("x-forwarded-proto") == "https"
            assert received_headers.get("x-forwarded-host") == "az.pansis.site:8799"
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_public_server_info_hostname_only_drives_restart() -> None:
    """Switching between http and https (same host) must NOT be considered a
    public-info change – the sidecar resolves the protocol per request now."""

    https_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [
                (b"host", b"az.pansis.site:8799"),
                (b"x-forwarded-proto", b"https"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    http_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [
                (b"host", b"az.pansis.site:8799"),
                (b"x-forwarded-proto", b"http"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    https_info = web_proxy_router._public_server_info(https_request)
    http_info = web_proxy_router._public_server_info(http_request)
    # Protocol differs (used as sidecar env default), but hostname is the same.
    assert https_info[0] == http_info[0] == "az.pansis.site"
    assert https_info[2] == "https:"
    assert http_info[2] == "http:"


def test_ensure_sidecar_adopts_healthy_loopback_process_after_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """A replacement Uvicorn worker must reuse the prior worker's sidecar."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [(b"host", b"toolbox.example.com:5173")],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    monkeypatch.setattr(web_proxy_router, "_sidecar_process", None)
    monkeypatch.setattr(web_proxy_router, "_sidecar_public_info", None)
    monkeypatch.setattr(web_proxy_router, "_sidecar_ready", lambda: True)

    def must_not_spawn() -> None:
        raise AssertionError("a healthy inherited sidecar must not be restarted")

    monkeypatch.setattr(web_proxy_router, "_ensure_node_dependencies", must_not_spawn)
    web_proxy_router._ensure_sidecar(request)
    assert web_proxy_router._sidecar_public_info == ("toolbox.example.com", 5173, "http:")
