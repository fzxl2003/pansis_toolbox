"""Private reverse proxy for progress-monitor TensorBoard sessions.

This intentionally lives in this tool instead of importing the standalone
dashboard proxy.  It accepts only the progress monitor's ``/tpm-tb`` prefix
and verifies the owner before forwarding traffic to its private SSH tunnel.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from backend.app.core.config import get_settings
from backend.app.services.auth_service import get_user_by_session_token
from tools.tensorboard_progress_monitor.backend.service import get_tb_tunnel, is_tb_session_owner_by_proxy_id

_PATH = re.compile(r"^/tpm-tb/([A-Za-z0-9_-]{8,})(/|$)")
_HOP_HEADERS = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"})


def _session_id(path: str) -> str | None:
    match = _PATH.match(path)
    return match.group(1) if match else None


def _user_id(scope: dict[str, Any]) -> str | None:
    cookie_name = get_settings().session_cookie_name
    for key, value in scope.get("headers", []):
        if key.lower() != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            part = part.strip()
            if part.startswith(f"{cookie_name}="):
                user = get_user_by_session_token(part[len(cookie_name) + 1:])
                return user.id if user else None
    return None


async def _error(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, message: str) -> None:
    body = message.encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _proxy_http(scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]], local_port: int) -> None:
    chunks: list[bytes] = []
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            return
        if event["type"] == "http.request":
            chunks.append(event.get("body", b""))
            if not event.get("more_body", False):
                break
    body = b"".join(chunks)
    headers: list[tuple[str, str]] = []
    for key, value in scope["headers"]:
        text_key = key.decode("ascii")
        if text_key.lower() not in _HOP_HEADERS | {"host", "cookie", "expect", "content-length"}:
            headers.append((text_key, value.decode("latin-1")))
    headers.extend([("Host", f"127.0.0.1:{local_port}"), ("Connection", "close"), ("Content-Length", str(len(body)))])
    query = scope.get("query_string", b"").decode("latin-1")
    target = scope["path"] + (f"?{query}" if query else "")
    payload = f"{scope['method']} {target} HTTP/1.1\r\n".encode("ascii")
    payload += "".join(f"{key}: {value}\r\n" for key, value in headers).encode("latin-1") + b"\r\n" + body
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
    except OSError:
        await _error(send, 502, "TensorBoard 隧道不可用")
        return
    try:
        writer.write(payload); await writer.drain()
        status_line = await reader.readline()
        fields = status_line.decode("ascii", "replace").split(" ", 2)
        if len(fields) < 2 or not fields[1].isdigit():
            await _error(send, 502, "TensorBoard 响应格式错误")
            return
        response_headers: list[tuple[bytes, bytes]] = []
        content_length: int | None = None
        chunked = False
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, separator, value = line.rstrip(b"\r\n").partition(b": ")
            if not separator:
                continue
            if key.lower() == b"transfer-encoding" and b"chunked" in value.lower(): chunked = True
            if key.decode("ascii", "replace").lower() in _HOP_HEADERS: continue
            if key.lower() == b"content-length":
                try: content_length = int(value)
                except ValueError: content_length = None
            response_headers.append((key, value))
        await send({"type": "http.response.start", "status": int(fields[1]), "headers": response_headers})
        if chunked:
            while True:
                line = await reader.readline()
                if not line: break
                try: size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError: break
                if size == 0:
                    while await reader.readline() not in (b"\r\n", b"\n", b""): pass
                    break
                chunk = await reader.readexactly(size); await reader.readexactly(2)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        else:
            remaining = content_length
            while remaining is None or remaining > 0:
                chunk = await reader.read(min(65536, remaining) if remaining is not None else 65536)
                if not chunk: break
                if remaining is not None: remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass


class TensorBoardProgressProxy:
    def __init__(self, app: Any): self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not _PATH.match(scope.get("path", "")):
            await self.app(scope, receive, send); return
        proxy_id = _session_id(scope["path"]); user_id = _user_id(scope)
        if not proxy_id or not user_id or not is_tb_session_owner_by_proxy_id(proxy_id, user_id):
            await _error(send, 403, "无权访问该 TensorBoard 会话"); return
        tunnel = get_tb_tunnel(proxy_id)
        if tunnel is None or not tunnel.is_alive():
            await _error(send, 502, "TensorBoard 会话未运行或隧道已断开"); return
        await _proxy_http(scope, receive, send, tunnel.local_port)


def mount_extra(app: FastAPI) -> None:
    app.add_middleware(TensorBoardProgressProxy)
