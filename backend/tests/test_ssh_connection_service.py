from __future__ import annotations

import threading
import time

from backend.app.services.ssh_connection_service import SSHConnectionPool, SSHConnectionSpec


class FakeTransport:
    def __init__(self) -> None:
        self.active = True
        self.keepalive: int | None = None

    def is_active(self) -> bool:
        return self.active

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive = seconds


class FakeChannel:
    def __init__(self, exit_status: int = 0) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class FakeStream:
    def __init__(self, text: str, exit_status: int = 0) -> None:
        self.text = text
        self.channel = FakeChannel(exit_status)

    def read(self) -> bytes:
        return self.text.encode()


class FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.transport = FakeTransport()
        self.closed = False
        self.commands: list[str] = []
        self.command_started = threading.Event()
        self.release_command = threading.Event()
        self.block_commands = False

    def set_missing_host_key_policy(self, policy: object) -> None:
        self.policy = policy

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def get_transport(self) -> FakeTransport:
        return self.transport

    def exec_command(self, command: str, timeout: int = 30):
        self.commands.append(command)
        self.command_started.set()
        if self.block_commands:
            self.release_command.wait(timeout=2)
        return None, FakeStream(f"{self.name}:{command}"), FakeStream("")

    def close(self) -> None:
        self.closed = True
        self.transport.active = False


def _spec(fingerprint: str = "auth") -> SSHConnectionSpec:
    return SSHConnectionSpec(
        tool_id="test_tool",
        server_id="srv_1",
        host="127.0.0.1",
        port=22,
        username="alice",
        auth_fingerprint=fingerprint,
        password="secret",
    )


def test_reuses_client_for_sequential_commands() -> None:
    created: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(f"client{len(created)}")
        created.append(client)
        return client

    pool = SSHConnectionPool(client_factory=factory)

    assert pool.exec_command(_spec(), "one")[0] == "client0:one"
    assert pool.exec_command(_spec(), "two")[0] == "client0:two"

    assert len(created) == 1
    assert created[0].transport.keepalive == 30
    assert not created[0].closed


def test_reconnects_when_transport_is_inactive() -> None:
    created: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(f"client{len(created)}")
        created.append(client)
        return client

    pool = SSHConnectionPool(client_factory=factory)

    pool.exec_command(_spec(), "one")
    created[0].transport.active = False

    assert pool.exec_command(_spec(), "two")[0] == "client1:two"
    assert len(created) == 2
    assert created[0].closed


def test_invalidate_closes_cached_client() -> None:
    created: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(f"client{len(created)}")
        created.append(client)
        return client

    pool = SSHConnectionPool(client_factory=factory)
    pool.exec_command(_spec(), "one")

    pool.invalidate(tool_id="test_tool", server_id="srv_1")

    assert created[0].closed
    assert pool.exec_command(_spec(), "two")[0] == "client1:two"


def test_concurrent_commands_use_different_clients_until_pool_limit() -> None:
    created: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(f"client{len(created)}")
        if not created:
            client.block_commands = True
        created.append(client)
        return client

    pool = SSHConnectionPool(max_clients_per_key=2, client_factory=factory)
    first_result: list[str] = []

    def run_first() -> None:
        first_result.append(pool.exec_command(_spec(), "slow")[0])

    thread = threading.Thread(target=run_first)
    thread.start()
    deadline = time.monotonic() + 2
    while not created and time.monotonic() < deadline:
        time.sleep(0.01)
    created[0].command_started.wait(timeout=2)

    second = pool.exec_command(_spec(), "fast")[0]
    created[0].release_command.set()
    thread.join(timeout=2)

    assert first_result == ["client0:slow"]
    assert second == "client1:fast"
    assert len(created) == 2
    assert created[0].commands == ["slow"]
    assert created[1].commands == ["fast"]


def test_pool_waits_instead_of_reusing_busy_single_client() -> None:
    created = [FakeClient("client0")]
    created[0].block_commands = True
    pool = SSHConnectionPool(max_clients_per_key=1, client_factory=lambda: created[0])
    first_result: list[str] = []
    second_result: list[str] = []

    thread = threading.Thread(target=lambda: first_result.append(pool.exec_command(_spec(), "slow")[0]))
    thread.start()
    created[0].command_started.wait(timeout=2)

    second = threading.Thread(target=lambda: second_result.append(pool.exec_command(_spec(), "after")[0]))
    second.start()
    time.sleep(0.05)
    assert second_result == []

    created[0].release_command.set()
    thread.join(timeout=2)
    second.join(timeout=2)

    assert first_result == ["client0:slow"]
    assert second_result == ["client0:after"]
