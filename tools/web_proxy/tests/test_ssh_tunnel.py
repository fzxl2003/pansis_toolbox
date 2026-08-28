import socket
import threading

from tools.web_proxy.backend.router import _probe_site_through_tunnel
from tools.web_proxy.backend.ssh_tunnel import SshHttpTunnel


class _FakeTransport:
    def __init__(self) -> None:
        self.destinations: list[tuple[str, int]] = []
        self.requests: list[bytes] = []

    def is_active(self) -> bool:
        return True

    def open_channel(self, _kind, destination, _source):
        self.destinations.append(destination)
        local, remote = socket.socketpair()

        def target() -> None:
            data = bytearray(remote.recv(4096))
            if bytes(data).startswith(b"GET "):
                while b"\r\n\r\n" not in data:
                    data.extend(remote.recv(4096))
                self.requests.append(bytes(data))
                remote.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            else:
                self.requests.append(bytes(data))
                remote.sendall(bytes(data).upper())
            remote.close()

        threading.Thread(target=target, daemon=True).start()
        return local


class _FakeClient:
    def __init__(self, transport: _FakeTransport) -> None:
        self.transport = transport

    def get_transport(self):
        return self.transport

    def close(self) -> None:
        pass


def _started_tunnel() -> tuple[SshHttpTunnel, _FakeTransport]:
    transport = _FakeTransport()
    tunnel = SshHttpTunnel("user", {
        "id": "server-id", "host": "ssh.example", "port": 22, "sshUsername": "proxy", "authType": "password", "sshPassword": "secret",
    })
    tunnel._client = _FakeClient(transport)
    tunnel._connect = lambda: None  # type: ignore[method-assign]
    tunnel.start()
    return tunnel, transport


def test_connect_traffic_uses_ssh_direct_tcpip() -> None:
    tunnel, transport = _started_tunnel()
    try:
        with socket.create_connection(("127.0.0.1", tunnel.port), timeout=2) as client:
            client.sendall(b"CONNECT target.example:443 HTTP/1.1\r\nHost: target.example:443\r\n\r\n")
            assert b"200 Connection Established" in client.recv(4096)
            client.sendall(b"hello over ssh")
            assert client.recv(4096) == b"HELLO OVER SSH"
        assert transport.destinations == [("target.example", 443)]
    finally:
        tunnel.stop()


def test_absolute_http_request_is_rewritten_and_uses_ssh() -> None:
    tunnel, transport = _started_tunnel()
    try:
        with socket.create_connection(("127.0.0.1", tunnel.port), timeout=2) as client:
            client.sendall(b"GET http://target.example:8080/path?q=1 HTTP/1.1\r\nHost: target.example\r\nProxy-Connection: keep-alive\r\n\r\n")
            assert b"200 OK" in client.recv(4096)
        assert transport.destinations == [("target.example", 8080)]
        assert transport.requests[0].startswith(b"GET /path?q=1 HTTP/1.1")
        assert b"Proxy-Connection" not in transport.requests[0]
    finally:
        tunnel.stop()


def test_exit_probe_uses_the_ssh_tunnel_instead_of_direct_egress() -> None:
    tunnel, transport = _started_tunnel()
    try:
        result = _probe_site_through_tunnel(tunnel.proxy_url, "http://target.example/health")
        assert result["reachable"] is True
        assert result["statusCode"] == 200
        assert transport.destinations == [("target.example", 80)]
        assert transport.requests[0].startswith(b"GET /health HTTP/1.1")
    finally:
        tunnel.stop()
