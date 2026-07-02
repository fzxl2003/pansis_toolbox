from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database
from backend.app.services.auth_service import User, login
from tools.ssh_workspace.backend import service


USER_A = User(id="ssh_ws_user_a", username="ssh_a", display_name="SSH A")
USER_B = User(id="ssh_ws_user_b", username="ssh_b", display_name="SSH B")


@pytest.fixture(autouse=True)
def ssh_workspace_data() -> None:
    init_database()
    service.init_database()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as conn:
        for table in (
            "ssh_task_runs",
            "ssh_scheduled_tasks",
            "ssh_screen_sessions",
            "ssh_command_templates",
            "ssh_command_history",
            "ssh_servers",
        ):
            conn.execute("DELETE FROM " + table + " WHERE owner_user_id IN (?, ?)", (USER_A.id, USER_B.id))
        conn.execute("DELETE FROM ssh_servers WHERE name LIKE 'WS Auth Test%'")
        conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = 'admin')")


def test_server_credentials_are_encrypted_and_user_scoped() -> None:
    created_a = service.create_server(
        {
            "name": "A",
            "host": "127.0.0.1",
            "port": 22,
            "sshUsername": "alice",
            "authType": "password",
            "sshPassword": "secret-a",
        },
        USER_A,
    )
    created_b = service.create_server(
        {
            "name": "B",
            "host": "127.0.0.2",
            "port": 22,
            "sshUsername": "bob",
            "authType": "private_key",
            "privateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----",
            "privateKeyPassphrase": "key-pass",
        },
        USER_B,
    )

    assert "sshPassword" not in created_a
    assert "privateKey" not in created_b
    assert [server["id"] for server in service.list_servers(USER_A)] == [created_a["id"]]

    with pytest.raises(ToolboxError) as exc:
        service.get_server(created_b["id"], USER_A)
    assert exc.value.status_code == 404

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM ssh_servers WHERE id = ?", (created_a["id"],)).fetchone()
    assert row["ssh_password_encrypted"] != "secret-a"
    assert service._decrypt(row["ssh_password_encrypted"]) == "secret-a"


def test_screen_parser_and_gate() -> None:
    parsed = service.parse_screen_ls(
        """
        There are screens on:
            1234.train_run    (Detached)
            5678.shell        (Attached)
        2 Sockets in /run/screen/S-user.
        """
    )
    assert parsed == [
        {"sessionName": "train_run", "status": "running", "state": "Detached"},
        {"sessionName": "shell", "status": "running", "state": "Attached"},
    ]
    assert service.parse_screen_ls("No Sockets found in /run/screen/S-user.\n") == []

    server = service.create_server(
        {
            "name": "No Screen",
            "host": "127.0.0.1",
            "sshUsername": "alice",
            "authType": "password",
            "sshPassword": "secret",
        },
        USER_A,
    )
    with pytest.raises(ToolboxError) as exc:
        service.create_screen_session(server["id"], {"name": "job"}, USER_A)
    assert exc.value.code == "SCREEN_UNAVAILABLE"


def test_templates_and_history_are_user_scoped() -> None:
    server = service.create_server(
        {
            "name": "History Server",
            "host": "127.0.0.1",
            "sshUsername": "alice",
            "authType": "password",
            "sshPassword": "secret",
        },
        USER_A,
    )
    template = service.create_template({"name": "Train", "command": "python train.py --lr {{lr}}"}, USER_A)
    history = service.record_history({"serverId": server["id"], "command": "pwd", "source": "terminal"}, USER_A)

    assert template["variables"] == ["lr"]
    assert service.list_templates(USER_B) == []
    assert service.list_history(USER_A)[0]["id"] == history["id"]
    assert service.list_history(USER_B) == []


def test_due_scheduler_launches_remote_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    server = service.create_server(
        {
            "name": "Screen Server",
            "host": "127.0.0.1",
            "sshUsername": "alice",
            "authType": "password",
            "sshPassword": "secret",
        },
        USER_A,
    )
    with get_connection() as conn:
        conn.execute("UPDATE ssh_servers SET has_screen = 1 WHERE id = ?", (server["id"],))
        conn.commit()
    task = service.create_scheduled_task(
        {
            "serverId": server["id"],
            "name": "Heartbeat",
            "command": "echo ok",
            "intervalSeconds": 60,
            "screenNamePrefix": "hb",
            "enabled": True,
        },
        USER_A,
    )
    with get_connection() as conn:
        conn.execute("UPDATE ssh_scheduled_tasks SET next_run_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (task["id"],))
        conn.commit()

    commands: list[str] = []
    monkeypatch.setattr(service, "_run_ssh", lambda row, command, timeout=30: commands.append(command) or "")

    service.collect_due_tasks()

    assert commands
    assert commands[0].startswith("screen -dmS hb_")
    assert "bash -lc 'echo ok'" in commands[0]
    runs = service.list_task_runs(task["id"], USER_A)
    assert runs[0]["status"] == "started"
    assert service.list_history(USER_A)[0]["source"] == "scheduled_task"


class FakeChannel:
    def __init__(self) -> None:
        self.resizes: list[tuple[int, int]] = []
        self.sent: list[Any] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def recv_ready(self) -> bool:
        return False

    def recv(self, size: int) -> bytes:
        return b""

    def send(self, data: Any) -> None:
        self.sent.append(data)

    def resize_pty(self, width: int, height: int) -> None:
        self.resizes.append((width, height))

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.closed = False

    def invoke_shell(self, term: str) -> FakeChannel:
        return self.channel

    def close(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = cookies or {}
        self.accepted = False
        self.close_code: int | None = None
        self.sent_json: list[dict[str, Any]] = []
        self._messages = [
            {"text": '{"type":"resize","cols":100,"rows":32}'},
            WebSocketDisconnect(),
        ]

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent_json.append(payload)

    async def receive(self) -> dict[str, Any]:
        item = self._messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_terminal_websocket_rejects_anonymous() -> None:
    socket = FakeWebSocket()

    asyncio.run(service.terminal_websocket(socket, "missing"))

    assert socket.accepted is False
    assert socket.close_code == 4401


def test_terminal_websocket_opens_shell_and_resizes(monkeypatch: pytest.MonkeyPatch) -> None:
    user, token = login("admin", "admin123")
    server = service.create_server(
        {
            "name": "WS Auth Test",
            "host": "127.0.0.1",
            "sshUsername": "admin",
            "authType": "password",
            "sshPassword": "secret",
        },
        user,
    )
    channel = FakeChannel()
    client = FakeClient(channel)
    monkeypatch.setattr(service, "_ssh_connect", lambda row, timeout=20: client)

    socket = FakeWebSocket({get_settings().session_cookie_name: token})
    asyncio.run(service.terminal_websocket(socket, server["id"]))

    assert socket.accepted is True
    assert socket.sent_json[0] == {"type": "status", "status": "connected"}
    assert channel.resizes == [(100, 32)]
    assert channel.closed is True
    assert client.closed is True
