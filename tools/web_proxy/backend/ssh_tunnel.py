"""Loopback-only HTTP proxy whose outbound connections use an SSH server.

Rammerhead supports an HTTP external proxy.  This adapter provides one locally
and turns every CONNECT/absolute-form HTTP request into a Paramiko
``direct-tcpip`` channel, so no proxy daemon is required on the exit server.
"""
from __future__ import annotations

import atexit
import select
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from backend.app.core.errors import ToolboxError
from backend.app.services.ssh_server_service import load_private_key


_MAX_HEADER_BYTES = 64 * 1024


class _TunnelRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.server.owner.handle_client(self.request, self.client_address)  # type: ignore[attr-defined]


class _LoopbackServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, owner: "SshHttpTunnel") -> None:
        self.owner = owner
        super().__init__(("127.0.0.1", 0), _TunnelRequestHandler)


@dataclass
class SshHttpTunnel:
    user_id: str
    server: dict[str, Any]
    _server: _LoopbackServer | None = None
    _thread: threading.Thread | None = None
    _client: Any | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("SSH tunnel has not started")
        return int(self._server.server_address[1])

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            self._connect()
            try:
                self._server = _LoopbackServer(self)
                self._thread = threading.Thread(target=self._server.serve_forever, name=f"web-proxy-ssh-{self.server['id'][:8]}", daemon=True)
                self._thread.start()
            except Exception:
                self._close_client()
                self._server = None
                raise

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
            self._close_client()

    def _connect(self) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖，无法建立 SSH 出口", status_code=500, tool_id="web_proxy") from exc
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self.server["host"], "port": int(self.server["port"]), "username": self.server["sshUsername"],
            "timeout": 15, "banner_timeout": 15, "auth_timeout": 15, "look_for_keys": False, "allow_agent": False,
        }
        try:
            if self.server["authType"] == "private_key":
                kwargs["pkey"] = load_private_key(self.server.get("privateKey", ""), self.server.get("privateKeyPassphrase", ""), paramiko)
            else:
                kwargs["password"] = self.server.get("sshPassword", "")
            client.connect(**kwargs)
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RuntimeError("SSH transport is inactive")
            transport.set_keepalive(30)
            self._client = client
        except ToolboxError:
            client.close()
            raise
        except Exception as exc:
            client.close()
            raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 出口连接失败: {exc}", status_code=502, tool_id="web_proxy") from exc

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def _open_channel(self, host: str, port: int, source: tuple[str, int]) -> Any:
        with self._lock:
            transport = self._client.get_transport() if self._client is not None else None
            if transport is None or not transport.is_active():
                self._close_client()
                self._connect()
                transport = self._client.get_transport()
            try:
                return transport.open_channel("direct-tcpip", (host, port), source)
            except Exception as exc:
                raise ToolboxError("SSH_EXIT_CONNECT_FAILED", f"SSH 出口无法连接目标 {host}:{port}: {exc}", status_code=502, tool_id="web_proxy") from exc

    def handle_client(self, client: socket.socket, address: tuple[str, int]) -> None:
        client.settimeout(30)
        channel = None
        try:
            header, remainder = _read_request_header(client)
            first, separator, rest = header.partition(b"\r\n")
            if not separator:
                raise ValueError("invalid proxy request")
            method, target, version = first.decode("latin-1").split(" ", 2)
            if method.upper() == "CONNECT":
                host, port = _parse_authority(target, 443)
                channel = self._open_channel(host, port, address)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if remainder:
                    channel.sendall(remainder)
            else:
                parsed = urlsplit(target)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("only absolute http(s) proxy requests are supported")
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
                channel = self._open_channel(host, port, address)
                clean_headers = _strip_proxy_headers(rest)
                channel.sendall(f"{method} {path} {version}\r\n".encode("latin-1") + clean_headers + b"\r\n\r\n" + remainder)
            _relay(client, channel)
        except (ToolboxError, OSError, ValueError) as exc:
            try:
                client.sendall(f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nX-Web-Proxy-Error: {type(exc).__name__}\r\n\r\n".encode("ascii"))
            except OSError:
                pass
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            try:
                client.close()
            except OSError:
                pass


def _read_request_header(client: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            raise ValueError("connection closed before proxy request headers")
        data.extend(chunk)
        if len(data) > _MAX_HEADER_BYTES:
            raise ValueError("proxy request headers too large")
    head, remainder = bytes(data).split(b"\r\n\r\n", 1)
    return head, remainder


def _parse_authority(value: str, default_port: int) -> tuple[str, int]:
    parsed = urlsplit(f"//{value}")
    if not parsed.hostname:
        raise ValueError("missing destination host")
    return parsed.hostname, parsed.port or default_port


def _strip_proxy_headers(headers: bytes) -> bytes:
    """Do not expose headers that describe the local CONNECT hop to targets."""
    kept: list[bytes] = []
    for line in headers.split(b"\r\n"):
        name, separator, _value = line.partition(b":")
        if separator and name.strip().lower() in {b"proxy-connection", b"proxy-authorization"}:
            continue
        kept.append(line)
    return b"\r\n".join(kept)


def _relay(client: socket.socket, channel: Any) -> None:
    client.settimeout(None)
    while True:
        readable, _, _ = select.select([client, channel], [], [], 30)
        if not readable:
            continue
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (channel if source is client else client).sendall(data)


class SshTunnelRegistry:
    def __init__(self) -> None:
        self._tunnels: dict[tuple[str, str], SshHttpTunnel] = {}
        self._lock = threading.RLock()

    def ensure(self, user_id: str, server: dict[str, Any]) -> SshHttpTunnel:
        key = (user_id, server["id"])
        with self._lock:
            tunnel = self._tunnels.get(key)
            if tunnel is None:
                tunnel = SshHttpTunnel(user_id, server)
                self._tunnels[key] = tunnel
        try:
            tunnel.start()
            return tunnel
        except Exception:
            with self._lock:
                self._tunnels.pop(key, None)
            tunnel.stop()
            raise

    def stop(self, user_id: str, server_id: str) -> None:
        with self._lock:
            tunnel = self._tunnels.pop((user_id, server_id), None)
        if tunnel is not None:
            tunnel.stop()

    def stop_all(self) -> None:
        with self._lock:
            tunnels, self._tunnels = list(self._tunnels.values()), {}
        for tunnel in tunnels:
            tunnel.stop()


tunnel_registry = SshTunnelRegistry()
atexit.register(tunnel_registry.stop_all)
