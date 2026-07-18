from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_user
from tools.web_proxy.backend.service import (
    clear_session as clear_session_db,
    get_session as get_session_db,
    save_session as save_session_db,
)

router = APIRouter()

logger = logging.getLogger("toolbox.web_proxy")

TOOL_ID = "web_proxy"
SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 8787
SIDECAR_CROSS_PORT = 8788
SIDECAR_BASE_URL = f"http://{SIDECAR_HOST}:{SIDECAR_PORT}"
ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "rammerhead"
_sidecar_process: subprocess.Popen | None = None
_sidecar_lock = threading.Lock()
_sidecar_log_handle = None
_sidecar_public_info: tuple[str, int, str] | None = None


@router.get("", response_model=None)
@router.get("/", response_model=None)
def open_proxy(request: Request, url: str | None = None) -> HTMLResponse | RedirectResponse:
    user = get_optional_user(request)
    if user is None:
        return _login_page(request, url)
    if not url:
        return _direct_entry_page()

    target_url = _normalize_target_url(url)
    session_id = _get_or_create_session(user.id, request)
    _edit_session(session_id, enable_shuffling=False)
    return RedirectResponse(f"{_external_origin(request)}/{session_id}/{target_url}", status_code=302)


@router.get("/session")
def session_status(request: Request) -> dict[str, str | bool | None]:
    user = require_user(request)
    session = get_session_db(user.id)
    session_id = session["sessionId"] if session else None
    return {
        "active": bool(session_id and _sidecar_session_exists(session_id, request)),
        "sessionId": session_id,
        "sidecarUrl": SIDECAR_BASE_URL,
    }


@router.post("/session/clear")
def clear_session(request: Request) -> dict[str, bool]:
    user = require_user(request)
    session = get_session_db(user.id)
    if session:
        _delete_sidecar_session(session["sessionId"])
    clear_session_db(user.id)
    return {"cleared": True}


def _get_or_create_session(user_id: str, request: Request | None = None) -> str:
    session = get_session_db(user_id)
    session_id = session["sessionId"] if session else None
    if session_id and _sidecar_session_exists(session_id, request):
        return session_id

    session_id = _create_sidecar_session(request)
    save_session_db(user_id, session_id)
    return session_id


def _create_sidecar_session(request: Request | None = None) -> str:
    _ensure_sidecar(request)
    password = _sidecar_password()
    query = urlencode({"pwd": password})
    session_id = _sidecar_get(f"/newsession?{query}").strip()
    if not session_id:
        raise ToolboxError("WEB_PROXY_SESSION_FAILED", "代理会话创建失败", status_code=502, tool_id=TOOL_ID)
    return session_id


def _edit_session(session_id: str, enable_shuffling: bool) -> None:
    password = _sidecar_password()
    query = urlencode({"id": session_id, "enableShuffling": "1" if enable_shuffling else "0", "pwd": password})
    response = _sidecar_get(f"/editsession?{query}").strip()
    if response != "Success":
        raise ToolboxError("WEB_PROXY_SESSION_FAILED", "代理会话配置失败", status_code=502, tool_id=TOOL_ID)


def _delete_sidecar_session(session_id: str) -> None:
    _ensure_sidecar()
    password = _sidecar_password()
    query = urlencode({"id": session_id, "pwd": password})
    _sidecar_get(f"/deletesession?{query}")


def _sidecar_session_exists(session_id: str, request: Request | None = None) -> bool:
    _ensure_sidecar(request)
    query = urlencode({"id": session_id})
    return _sidecar_get(f"/sessionexists?{query}").strip() == "exists"


def _ensure_sidecar(request: Request | None = None) -> None:
    global _sidecar_log_handle, _sidecar_process
    global _sidecar_public_info
    with _sidecar_lock:
        public_info = _public_server_info(request)
        if (
            _sidecar_process is not None
            and _sidecar_process.poll() is None
            and _sidecar_ready()
            and (_sidecar_public_info is None or _sidecar_public_info[0] == public_info[0])
        ):
            return
        if _sidecar_process is not None and _sidecar_process.poll() is None:
            _sidecar_process.terminate()
            try:
                _sidecar_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _sidecar_process.kill()

        _ensure_node_dependencies()
        _ensure_sidecar_build()
        data_dir = _global_data_dir()
        (data_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (data_dir / "cache-js").mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "PANSIS_WEB_PROXY_DATA_DIR": str(data_dir),
                "PANSIS_WEB_PROXY_HOST": SIDECAR_HOST,
                "PANSIS_WEB_PROXY_PORT": str(SIDECAR_PORT),
                "PANSIS_WEB_PROXY_CROSS_PORT": str(SIDECAR_CROSS_PORT),
                "PANSIS_WEB_PROXY_PUBLIC_HOST": public_info[0],
                "PANSIS_WEB_PROXY_PUBLIC_PORT": str(public_info[1]),
                "PANSIS_WEB_PROXY_PUBLIC_PROTOCOL": public_info[2],
                "PANSIS_WEB_PROXY_PASSWORD": _sidecar_password(),
            }
        )
        if _sidecar_log_handle is None or _sidecar_log_handle.closed:
            _sidecar_log_handle = (_global_data_dir() / "sidecar.log").open("ab")
        _sidecar_process = subprocess.Popen(
            _runtime_command("node", "src/server.js"),
            cwd=VENDOR_DIR,
            env=env,
            stdout=_sidecar_log_handle,
            stderr=_sidecar_log_handle,
        )
        _sidecar_public_info = public_info
        _wait_for_sidecar()


def _ensure_node_dependencies() -> None:
    if (VENDOR_DIR / "node_modules" / "testcafe-hammerhead").exists() and (VENDOR_DIR / "node_modules" / "dotenv-flow").exists():
        return
    subprocess.run(
        _runtime_command("npm", "install", "--ignore-scripts"),
        cwd=VENDOR_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )


def _ensure_sidecar_build() -> None:
    if (VENDOR_DIR / "src" / "client" / "hammerhead.min.js").exists():
        return
    subprocess.run(
        _runtime_command("npm", "run", "build"),
        cwd=VENDOR_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )


def _runtime_command(name: str, *args: str) -> list[str]:
    if shutil.which(name):
        return [name, *args]
    conda = shutil.which("conda")
    if conda:
        return [conda, "run", "-n", "pansis_toolbox", name, *args]
    raise ToolboxError(
        "WEB_PROXY_RUNTIME_MISSING",
        f"未找到 {name}，请在 pansis_toolbox 环境中安装 Node.js",
        status_code=500,
        tool_id=TOOL_ID,
    )


def _wait_for_sidecar() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _sidecar_process is not None and _sidecar_process.poll() is not None:
            break
        if _sidecar_ready():
            return
        time.sleep(0.25)
    raise ToolboxError("WEB_PROXY_START_FAILED", "网页代理进程启动失败", status_code=502, tool_id=TOOL_ID)


def _sidecar_ready() -> bool:
    try:
        _sidecar_get("/needpassword", timeout=1)
        return True
    except ToolboxError:
        return False


def _sidecar_get(path: str, timeout: int = 10) -> str:
    request = UrlRequest(f"{SIDECAR_BASE_URL}{path}", headers={"User-Agent": "pansis-toolbox-web-proxy"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise ToolboxError("WEB_PROXY_UNAVAILABLE", "网页代理进程不可用", status_code=502, tool_id=TOOL_ID) from exc


def _global_data_dir() -> Path:
    return (get_settings().storage_dir / TOOL_ID).resolve()


def _sidecar_password() -> str:
    path = _global_data_dir() / "sidecar_password.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(32)
    path.write_text(password, encoding="utf-8")
    return password


def _normalize_target_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ToolboxError("WEB_PROXY_INVALID_URL", "请输入网址", status_code=400, tool_id=TOOL_ID)
    if value.startswith(("http://", "https://")):
        return value
    if "://" in value:
        raise ToolboxError("WEB_PROXY_INVALID_URL", "仅支持 http 和 https 网址", status_code=400, tool_id=TOOL_ID)
    return f"https://{value}"


def _external_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    if proto in ("ws", "ws:"):
        proto = "http"
    elif proto in ("wss", "wss:"):
        proto = "https"
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _public_server_info(request: Request | None) -> tuple[str, int, str]:
    """Return the public host/port the sidecar should rewrite URLs for.

    Since rammerhead's ``getServerInfo`` now reads ``X-Forwarded-Proto`` /
    ``X-Forwarded-Host`` per request, the protocol here is only used as a
    sensible default when the sidecar cannot determine the real protocol
    (e.g. direct localhost access).  The returned tuple is also used to decide
    whether the sidecar needs restarting: only the *hostname* matters for that
    decision, so a switch between http and https no longer causes a restart.
    """
    if request is None:
        return (SIDECAR_HOST, SIDECAR_PORT, "http:")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    # Normalise WebSocket schemes to their HTTP equivalents – the middleware may
    # call this from a websocket scope where ``request.url.scheme`` is ``ws``/
    # ``wss`` but the sidecar must rewrite URLs with ``http``/``https``.
    if proto in ("ws", "ws:"):
        proto = "http"
    elif proto in ("wss", "wss:"):
        proto = "https"
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    port = 443 if proto == "https" else 80
    hostname = host
    if host.startswith("[") and "]" in host:
        hostname, _, tail = host.partition("]")
        hostname = f"{hostname}]"
        if tail.startswith(":") and tail[1:].isdigit():
            port = int(tail[1:])
    elif ":" in host:
        hostname, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            port = int(maybe_port)
    return (hostname, port, f"{proto}:")


def _login_page(request: Request, url: str | None) -> HTMLResponse:
    query = f"?{urlencode({'url': url})}" if url else ""
    login_url = f"/login?redirect=/web-proxy{query}"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>需要登录</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 32px;">
  <h1>需要登录后使用网页代理</h1>
  <p>目标网站登录态会绑定到当前 toolbox 用户。</p>
  <p><a href="{login_url}">前往登录</a></p>
</body>
</html>""",
        status_code=401,
    )


def _direct_entry_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>网页代理</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 32px;">
  <form method="get" action="/web-proxy">
    <input name="url" placeholder="https://example.com" style="min-width: 360px; padding: 10px;">
    <button type="submit" style="padding: 10px 14px;">打开</button>
  </form>
</body>
</html>"""
    )


# ---------------------------------------------------------------------------
# Reverse proxy – forwards rammerhead traffic from the public toolbox origin
# to the local sidecar process (127.0.0.1:8787).
#
# The sidecar rewrites all URLs in proxied pages so that they point back to the
# toolbox's public origin (see ``_public_server_info``).  The toolbox backend
# must therefore reverse-proxy every request whose path belongs to rammerhead
# (session traffic ``/<sessionId>/...`` and the reserved client-script routes
# such as ``/hammerhead.js``) to the sidecar.  Without this proxy those paths
# fall through to the SPA catch-all and the browser shows a React Router 404.
# ---------------------------------------------------------------------------

# UUID v4 without dashes – 32 hex characters (matches rammerhead getSessionId).
_SESSION_ID_RE = re.compile(r"^/[0-9a-f]{32}(/|$)", re.IGNORECASE)

# Hammerhead / rammerhead reserved root-level routes that the browser loads
# directly (not under a session id prefix).
_RAMMERHEAD_ROOT_PATHS = frozenset(
    {
        "/rammerhead.js",
        "/hammerhead.js",
        "/worker-hammerhead.js",
        "/transport-worker.js",
        "/task.js",
        "/iframe-task.js",
        "/messaging",
        "/syncLocalStorage",
        "/api/shuffleDict",
    }
)

# Hop-by-hop headers that must not be forwarded by a proxy (RFC 7230 §6.1).
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# Extra request headers to strip before forwarding to the sidecar.
_STRIP_REQUEST_HEADERS = _HOP_BY_HOP_HEADERS | {"host", "expect"}


def _is_rammerhead_path(path: str) -> bool:
    """Return True when *path* should be served by the rammerhead sidecar."""
    if _SESSION_ID_RE.match(path):
        return True
    return path in _RAMMERHEAD_ROOT_PATHS


async def _ensure_sidecar_for_scope(scope: dict[str, Any]) -> None:
    """Make sure the sidecar process is running, using a fast in-process check.

    The full ``_ensure_sidecar`` acquires a lock and performs a blocking HTTP
    health-check on every call.  Once the sidecar is up we can skip that work
    on the hot path (every proxied asset request) and only fall back to the
    slow path when the process is missing or the public host changed.

    Only the *hostname* is compared – the sidecar resolves the protocol per
    request from ``X-Forwarded-Proto``, so switching between http and https
    must not trigger a restart.
    """
    request = Request(scope)
    public_info = _public_server_info(request)
    if (
        _sidecar_process is not None
        and _sidecar_process.poll() is None
        and _sidecar_public_info is not None
        and _sidecar_public_info[0] == public_info[0]
    ):
        return
    await asyncio.to_thread(_ensure_sidecar, request)


# -- HTTP proxy -------------------------------------------------------------


async def _proxy_http(scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    method = scope["method"]
    path = scope["path"]
    query_string = scope.get("query_string", b"")
    target = f"{path}?{query_string.decode('ascii')}" if query_string else path

    # Collect request headers, stripping hop-by-hop / host headers.
    req_headers: list[tuple[str, str]] = []
    for raw_key, raw_value in scope["headers"]:
        key = raw_key.decode("ascii")
        if key.lower() in _STRIP_REQUEST_HEADERS:
            continue
        req_headers.append((key, raw_value.decode("latin-1")))
    req_headers.append(("Host", f"{SIDECAR_HOST}:{SIDECAR_PORT}"))
    req_headers.append(("Connection", "close"))

    # Buffer the request body.  Rammerhead request bodies (form submits, AJAX
    # payloads) are small; streaming would complicate the raw-socket write for
    # little gain.
    body_chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        if message["type"] == "http.request":
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
    body = b"".join(body_chunks)
    if body or method in ("POST", "PUT", "PATCH"):
        req_headers.append(("Content-Length", str(len(body))))

    request_line = f"{method} {target} HTTP/1.1\r\n".encode("ascii")
    header_block = "".join(f"{k}: {v}\r\n" for k, v in req_headers).encode("latin-1")
    request_bytes = request_line + header_block + b"\r\n" + body

    try:
        reader, writer = await asyncio.open_connection(SIDECAR_HOST, SIDECAR_PORT)
    except OSError:
        await _send_http_error(send, 502, "网页代理进程不可用")
        return

    try:
        writer.write(request_bytes)
        await writer.drain()

        # --- response status line ---
        status_line = await reader.readline()
        if not status_line:
            await _send_http_error(send, 502, "网页代理进程返回空响应")
            return
        parts = status_line.decode("ascii").strip().split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            await _send_http_error(send, 502, "网页代理进程响应格式错误")
            return
        status_code = int(parts[1])

        # --- response headers ---
        resp_headers: list[tuple[bytes, bytes]] = []
        transfer_encoding: str | None = None
        content_length: int | None = None
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, sep, val = line.rstrip(b"\r\n").partition(b": ")
            if not sep:
                continue
            key_lower = key.decode("ascii").lower()
            if key_lower in _HOP_BY_HOP_HEADERS:
                if key_lower == "transfer-encoding":
                    transfer_encoding = val.decode("latin-1").lower()
                continue
            if key_lower == "content-length":
                try:
                    content_length = int(val)
                except ValueError:
                    content_length = None
            resp_headers.append((key, val))

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": resp_headers,
            }
        )

        # --- response body (streamed) ---
        if transfer_encoding and "chunked" in transfer_encoding:
            await _stream_chunked(reader, send)
        elif content_length is not None:
            await _stream_fixed(reader, send, content_length)
        else:
            await _stream_until_eof(reader, send)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        # Connection broke mid-stream – tell the client we're done.
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def _stream_chunked(
    reader: asyncio.StreamReader,
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    while True:
        size_line = await reader.readline()
        if not size_line:
            break
        size_str = size_line.decode("ascii").strip().split(";")[0]
        try:
            chunk_size = int(size_str, 16)
        except ValueError:
            break
        if chunk_size == 0:
            # consume trailing headers up to the final CRLF
            while True:
                trailer = await reader.readline()
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break
        chunk = await reader.readexactly(chunk_size)
        await reader.readexactly(2)  # CRLF after chunk data
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _stream_fixed(
    reader: asyncio.StreamReader,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    length: int,
) -> None:
    remaining = length
    while remaining > 0:
        chunk = await reader.read(min(remaining, 65536))
        if not chunk:
            break
        remaining -= len(chunk)
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _stream_until_eof(
    reader: asyncio.StreamReader,
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _send_http_error(send: Callable[[dict[str, Any]], Awaitable[None]], status_code: int, message: str) -> None:
    body = message.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# -- WebSocket proxy --------------------------------------------------------


async def _proxy_websocket(
    scope: dict[str, Any],
    receive: Callable[[], Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    path = scope["path"]
    query_string = scope.get("query_string", b"")
    target = f"{path}?{query_string.decode('ascii')}" if query_string else path

    try:
        reader, writer = await asyncio.open_connection(SIDECAR_HOST, SIDECAR_PORT)
    except OSError:
        await send({"type": "websocket.close", "code": 1011})
        return

    # --- client-side WebSocket handshake to the sidecar ---
    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    handshake_lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {SIDECAR_HOST}:{SIDECAR_PORT}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {ws_key}",
        "Sec-WebSocket-Version: 13",
    ]
    for raw_key, raw_value in scope["headers"]:
        key = raw_key.decode("ascii")
        if key.lower() in _STRIP_REQUEST_HEADERS or key.lower() in (
            "sec-websocket-key",
            "sec-websocket-version",
        ):
            continue
        handshake_lines.append(f"{key}: {raw_value.decode('latin-1')}")
    handshake = "\r\n".join(handshake_lines) + "\r\n\r\n"

    writer.write(handshake.encode("latin-1"))
    await writer.drain()

    status_line = await reader.readline()
    status_parts = status_line.decode("ascii").strip().split(" ", 2)
    if len(status_parts) < 2 or status_parts[1] != "101":
        writer.close()
        await send({"type": "websocket.close", "code": 1011})
        return

    resp_subprotocol: str | None = None
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        key, sep, val = line.rstrip(b"\r\n").partition(b": ")
        if sep and key.decode("ascii").lower() == "sec-websocket-protocol":
            resp_subprotocol = val.decode("latin-1")

    accept_msg: dict[str, Any] = {"type": "websocket.accept"}
    if resp_subprotocol:
        accept_msg["subprotocol"] = resp_subprotocol
    await send(accept_msg)

    # --- bidirectional pipe ---
    async def browser_to_sidecar() -> None:
        try:
            while True:
                message = await receive()
                mtype = message.get("type")
                if mtype == "websocket.disconnect":
                    break
                if mtype == "websocket.receive":
                    data = message.get("bytes")
                    if data is not None:
                        await _send_ws_frame(writer, 0x2, data)
                    elif message.get("text") is not None:
                        await _send_ws_frame(writer, 0x1, message["text"].encode("utf-8"))
        except Exception:  # noqa: BLE001
            pass

    async def sidecar_to_browser() -> None:
        try:
            while True:
                opcode, payload = await _recv_ws_frame(reader)
                if opcode == 0x1:  # text
                    await send({"type": "websocket.send", "text": payload.decode("utf-8")})
                elif opcode == 0x2:  # binary
                    await send({"type": "websocket.send", "bytes": payload})
                elif opcode == 0x8:  # close
                    code = 1000
                    if len(payload) >= 2:
                        code = struct.unpack("!H", payload[:2])[0]
                    await send({"type": "websocket.close", "code": code})
                    break
                elif opcode == 0x9:  # ping → pong
                    await _send_ws_frame(writer, 0xA, payload)
                # 0x0 (continuation) and 0xA (pong) are ignored; rammerhead
                # sends unfragmented frames so we don't need reassembly.
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:  # noqa: BLE001
            pass

    browser_task = asyncio.create_task(browser_to_sidecar())
    sidecar_task = asyncio.create_task(sidecar_to_browser())
    done, pending = await asyncio.wait({browser_task, sidecar_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, (asyncio.CancelledError,)):
            logger.debug("websocket proxy task ended: %r", exc)

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


def _apply_mask(payload: bytes, mask: bytes) -> bytes:
    """Apply/remove the WebSocket XOR masking (RFC 6455 §5.3)."""
    if not payload:
        return b""
    result = bytearray(payload)
    for i in range(4):
        result[i::4] = bytes(b ^ mask[i] for b in payload[i::4])
    return bytes(result)


async def _send_ws_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
    """Send a single masked WebSocket frame (client → server)."""
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])  # FIN + opcode
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)  # MASK bit set
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask)
    writer.write(bytes(header))
    if payload:
        writer.write(_apply_mask(payload, mask))
    await writer.drain()


async def _recv_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read a single WebSocket frame (server → client, unmasked)."""
    data = await reader.readexactly(2)
    opcode = data[0] & 0x0F
    masked = data[1] & 0x80
    length = data[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length > 0 else b""
    if masked:
        payload = _apply_mask(payload, mask)
    return opcode, payload


# -- ASGI middleware --------------------------------------------------------

class RammerheadProxyMiddleware:
    """ASGI middleware that reverse-proxies rammerhead traffic to the sidecar.

    Runs as a pure ASGI middleware (not ``BaseHTTPMiddleware``) so that both
    HTTP and WebSocket scopes are intercepted and request/response bodies can
    be streamed without buffering.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if not _is_rammerhead_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        try:
            await _ensure_sidecar_for_scope(scope)
        except ToolboxError as exc:
            if scope["type"] == "http":
                await _send_http_error(send, exc.status_code, exc.message)
            else:
                await send({"type": "websocket.close", "code": 1011})
            return
        except Exception:  # noqa: BLE001
            if scope["type"] == "http":
                await _send_http_error(send, 502, "网页代理进程启动失败")
            else:
                await send({"type": "websocket.close", "code": 1011})
            return

        if scope["type"] == "http":
            await _proxy_http(scope, receive, send)
        else:
            await _proxy_websocket(scope, receive, send)


def mount_extra(app: FastAPI) -> None:
    """Register root-level middleware on the host application.

    Called by the tool loader after the tool's API router has been mounted.
    The middleware must be added *after* CORS so that it becomes the outermost
    layer and rammerhead traffic is intercepted before any other middleware.
    """
    app.add_middleware(RammerheadProxyMiddleware)
