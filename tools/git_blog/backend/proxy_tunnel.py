"""Small stdlib-only ProxyCommand used for GitHub SSH through a user proxy."""
from __future__ import annotations

import os
import select
import socket
import ssl
import sys
from urllib.parse import unquote, urlparse


def _read_until(sock: socket.socket, marker: bytes) -> bytes:
    data = b""
    while marker not in data and len(data) < 16 * 1024:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _connect_http(proxy: object, host: str, port: int) -> socket.socket:
    parsed = proxy  # Kept separate to make the tunnel protocol obvious.
    sock = socket.create_connection((parsed.hostname, parsed.port or 8080), timeout=30)
    if parsed.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
    credentials = ""
    if parsed.username:
        import base64
        credentials = "Proxy-Authorization: Basic " + base64.b64encode(
            f"{unquote(parsed.username)}:{unquote(parsed.password or '')}".encode()
        ).decode() + "\r\n"
    sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{credentials}\r\n".encode())
    response = _read_until(sock, b"\r\n\r\n")
    if not response.startswith(b"HTTP/") or b" 200 " not in response.split(b"\r\n", 1)[0]:
        raise RuntimeError("HTTP 代理 CONNECT 请求失败")
    return sock


def _connect_socks5(parsed: object, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((parsed.hostname, parsed.port or 1080), timeout=30)
    auth = bool(parsed.username)
    sock.sendall(b"\x05\x02\x00\x02" if auth else b"\x05\x01\x00")
    if sock.recv(2) == b"\x05\x02":
        username, password = unquote(parsed.username or "").encode(), unquote(parsed.password or "").encode()
        sock.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
        if sock.recv(2) != b"\x01\x00":
            raise RuntimeError("SOCKS5 代理认证失败")
    host_bytes = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, "big"))
    reply = sock.recv(4)
    if len(reply) != 4 or reply[1] != 0:
        raise RuntimeError("SOCKS5 代理连接失败")
    length = 4 if reply[3] == 1 else 16 if reply[3] == 4 else sock.recv(1)[0]
    sock.recv(length + 2)
    return sock


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    parsed = urlparse(os.environ.get("GIT_BLOG_PROXY_URL", ""))
    if not parsed.hostname:
        return 2
    host, port = sys.argv[1], int(sys.argv[2])
    sock = _connect_socks5(parsed, host, port) if parsed.scheme.startswith("socks5") else _connect_http(parsed, host, port)
    while True:
        readable, _, _ = select.select([sock, sys.stdin.buffer], [], [])
        if sock in readable:
            data = sock.recv(65536)
            if not data:
                return 0
            sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
        if sys.stdin.buffer in readable:
            data = sys.stdin.buffer.read1(65536)
            if not data:
                sock.shutdown(socket.SHUT_WR)
            else:
                sock.sendall(data)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GitHub proxy error: {exc}", file=sys.stderr)
        raise SystemExit(1)
