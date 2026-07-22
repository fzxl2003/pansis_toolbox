"""ASGI reverse-proxy middleware for TensorBoard sessions.

Intercepts paths matching ``/tb/{session_id}/*`` and forwards them to the
local SSH-tunneled port for the corresponding TensorBoard instance.  Both
HTTP and WebSocket traffic are supported.

The middleware verifies that the requesting user owns the session before
forwarding any traffic, so only the session creator can access it.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import struct
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from backend.app.core.config import get_settings
from backend.app.services.auth_service import get_user_by_session_token

from tools.tensorboard_dashboard.backend.service import (
    get_tunnel,
    is_session_owner_by_tb_id,
)

logger = logging.getLogger(__name__)

# Match /tb/{24-hex-chars} or /tb/{24-hex-chars}/...
_SESSION_PATH_RE = re.compile(r"^/tb/([0-9a-f]{24})(/|$)", re.IGNORECASE)

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

_STRIP_REQUEST_HEADERS = _HOP_BY_HOP_HEADERS | {"host", "expect", "cookie"}


def _is_tensorboard_path(path: str) -> bool:
    """Return True when *path* should be served by the TensorBoard proxy."""
    return bool(_SESSION_PATH_RE.match(path))


def _extract_session_id(path: str) -> str | None:
    """Extract the tb_session_id from a proxy path."""
    match = _SESSION_PATH_RE.match(path)
    return match.group(1) if match else None


def _get_session_token(scope: dict[str, Any]) -> str | None:
    """Extract the session token from the ASGI scope's cookie header."""
    cookie_name = get_settings().session_cookie_name
    for raw_key, raw_value in scope.get("headers", []):
        if raw_key.decode("ascii").lower() == "cookie":
            cookie_header = raw_value.decode("latin-1")
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(f"{cookie_name}="):
                    return part[len(cookie_name) + 1:]
    return None


def _authenticate(scope: dict[str, Any]) -> str | None:
    """Authenticate the user from the ASGI scope. Returns user_id or None."""
    token = _get_session_token(scope)
    if not token:
        return None
    user = get_user_by_session_token(token)
    return user.id if user else None


async def _proxy_http(
    scope: dict[str, Any],
    receive: Callable[[], Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
    local_port: int,
) -> None:
    """Forward an HTTP request to the local tunneled port."""
    method = scope["method"]
    path = scope["path"]
    query_string = scope.get("query_string", b"")
    target = f"{path}?{query_string.decode('ascii')}" if query_string else path

    # Collect request headers, stripping hop-by-hop / host / cookie headers.
    req_headers: list[tuple[str, str]] = []
    for raw_key, raw_value in scope["headers"]:
        key = raw_key.decode("ascii")
        if key.lower() in _STRIP_REQUEST_HEADERS:
            continue
        req_headers.append((key, raw_value.decode("latin-1")))
    req_headers.append(("Host", f"127.0.0.1:{local_port}"))
    req_headers.append(("Connection", "close"))

    # Buffer the request body.
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
        reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
    except OSError:
        await _send_http_error(send, 502, "TensorBoard 隧道不可用")
        return

    try:
        writer.write(request_bytes)
        await writer.drain()

        # --- response status line ---
        status_line = await reader.readline()
        if not status_line:
            await _send_http_error(send, 502, "TensorBoard 返回空响应")
            return
        parts = status_line.decode("ascii").strip().split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            await _send_http_error(send, 502, "TensorBoard 响应格式错误")
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
            while True:
                trailer = await reader.readline()
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break
        chunk = await reader.readexactly(chunk_size)
        await reader.readexactly(2)
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


async def _send_http_error(
    send: Callable[[dict[str, Any]], Awaitable[None]],
    status_code: int,
    message: str,
) -> None:
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
    local_port: int,
) -> None:
    """Forward a WebSocket connection to the local tunneled port."""
    path = scope["path"]
    query_string = scope.get("query_string", b"")
    target = f"{path}?{query_string.decode('ascii')}" if query_string else path

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
    except OSError:
        await send({"type": "websocket.close", "code": 1011})
        return

    # --- client-side WebSocket handshake to the local port ---
    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    handshake_lines = [
        f"GET {target} HTTP/1.1",
        f"Host: 127.0.0.1:{local_port}",
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
    async def browser_to_tb() -> None:
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

    async def tb_to_browser() -> None:
        try:
            while True:
                opcode, payload = await _recv_ws_frame(reader)
                if opcode == 0x1:
                    await send({"type": "websocket.send", "text": payload.decode("utf-8")})
                elif opcode == 0x2:
                    await send({"type": "websocket.send", "bytes": payload})
                elif opcode == 0x8:
                    code = 1000
                    if len(payload) >= 2:
                        code = struct.unpack("!H", payload[:2])[0]
                    await send({"type": "websocket.close", "code": code})
                    break
                elif opcode == 0x9:
                    await _send_ws_frame(writer, 0xA, payload)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:  # noqa: BLE001
            pass

    browser_task = asyncio.create_task(browser_to_tb())
    tb_task = asyncio.create_task(tb_to_browser())
    done, pending = await asyncio.wait({browser_task, tb_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            logger.debug("websocket proxy task ended: %r", exc)

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


def _apply_mask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return b""
    result = bytearray(payload)
    for i in range(4):
        result[i::4] = bytes(b ^ mask[i] for b in payload[i::4])
    return bytes(result)


async def _send_ws_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
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


class TensorBoardProxyMiddleware:
    """ASGI middleware that reverse-proxies TensorBoard traffic to local tunnels."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not _is_tensorboard_path(path):
            await self.app(scope, receive, send)
            return

        # Authenticate the user.
        user_id = _authenticate(scope)
        if user_id is None:
            if scope["type"] == "http":
                await _send_http_error(send, 401, "未登录或登录已过期")
            else:
                await send({"type": "websocket.close", "code": 4401})
            return

        # Verify session ownership.
        tb_session_id = _extract_session_id(path)
        if tb_session_id is None or not is_session_owner_by_tb_id(tb_session_id, user_id):
            if scope["type"] == "http":
                await _send_http_error(send, 403, "无权访问该 TensorBoard 会话")
            else:
                await send({"type": "websocket.close", "code": 4403})
            return

        # Look up the tunnel.
        tunnel = get_tunnel(tb_session_id)
        if tunnel is None or not tunnel.is_alive():
            if scope["type"] == "http":
                await _send_http_error(send, 502, "TensorBoard 隧道未建立或已断开，请重新启动会话")
            else:
                await send({"type": "websocket.close", "code": 1011})
            return

        local_port = tunnel.local_port

        if scope["type"] == "http":
            await _proxy_http(scope, receive, send, local_port)
        else:
            await _proxy_websocket(scope, receive, send, local_port)


def mount_extra(app: FastAPI) -> None:
    """Register root-level middleware on the host application."""
    app.add_middleware(TensorBoardProxyMiddleware)
