from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database, user_tool_connection_context
from backend.app.services.auth_service import User, login
from backend.app.services import ssh_server_service
from tools.ssh_workspace.backend import service


USER_A = User(id="ssh_ws_user_a", username="ssh_a", display_name="SSH A")
USER_B = User(id="ssh_ws_user_b", username="ssh_b", display_name="SSH B")


@pytest.fixture(autouse=True)
def ssh_workspace_data(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "platform_db_path", tmp_path / "platform.db")
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    service._initialized_dbs.clear()
    init_database()
    service.init_database(USER_A.id)
    service.init_database(USER_B.id)
    yield
    service._initialized_dbs.clear()


def add_workspace_server(user: User, *, name: str = "Server") -> dict[str, Any]:
    global_server = ssh_server_service.create_server(
        {
            "name": name, "host": "127.0.0.1", "port": 22, "sshUsername": "alice",
            "authType": "password", "sshPassword": "secret", "privateKey": "",
            "privateKeyPassphrase": "", "isPublic": False, "allowedUserIds": [],
        },
        user,
    )
    return service.create_server({"serverId": global_server["id"]}, user)


def test_workspace_lists_only_explicitly_added_global_servers() -> None:
    selected_a = add_workspace_server(USER_A, name="A")
    global_b = ssh_server_service.create_server(
        {
            "name": "B", "host": "127.0.0.2", "port": 22, "sshUsername": "bob",
            "authType": "password", "sshPassword": "secret-b", "privateKey": "",
            "privateKeyPassphrase": "", "isPublic": False, "allowedUserIds": [],
        },
        USER_A,
    )

    assert [server["id"] for server in service.list_servers(USER_A)] == [selected_a["id"]]
    assert global_b["id"] not in [server["id"] for server in service.list_servers(USER_A)]

    with pytest.raises(ToolboxError) as exc:
        service.get_server(global_b["id"], USER_A)
    assert exc.value.status_code == 404

    with user_tool_connection_context(USER_A.id, service.TOOL_ID) as conn:
        binding = conn.execute("SELECT * FROM ssh_servers WHERE id = ?", (selected_a["id"],)).fetchone()
    assert binding["ssh_password_encrypted"] == ""
    assert service.get_server(selected_a["id"], USER_A)["ssh_password"] == "secret"


def test_connection_test_persists_screen_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    server = add_workspace_server(USER_A, name="Screen capable")

    class Stream:
        class Channel:
            def recv_exit_status(self) -> int:
                return 0

        def __init__(self, output: str) -> None:
            self.output = output
            self.channel = self.Channel()

        def read(self) -> bytes:
            return self.output.encode()

    class TestClient:
        def exec_command(self, command: str, timeout: int = 30):
            output = "HAS_SCREEN\n" if "command -v screen" in command else "alice\n"
            return None, Stream(output), Stream("")

        def close(self) -> None:
            pass

    monkeypatch.setattr(service, "_ssh_connect", lambda row, timeout=20: TestClient())
    result = service.test_server(server["id"], USER_A)

    assert result == {"connected": True, "hasScreen": True}
    listed = service.list_servers(USER_A)
    assert listed[0]["hasScreen"] is True
    assert listed[0]["lastTestStatus"] == "ok"
    assert service.get_server(server["id"], USER_A)["has_screen"] == 1


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
        {"sessionName": "train_run", "pid": "1234", "status": "running", "state": "Detached"},
        {"sessionName": "shell", "pid": "5678", "status": "running", "state": "Attached"},
    ]
    assert service.parse_screen_ls("No Sockets found in /run/screen/S-user.\n") == []

    server = add_workspace_server(USER_A, name="No Screen")
    with pytest.raises(ToolboxError) as exc:
        service.create_screen_session(server["id"], {"name": "job"}, USER_A)
    assert exc.value.code == "SCREEN_UNAVAILABLE"


def test_templates_and_history_are_user_scoped() -> None:
    server = add_workspace_server(USER_A, name="History Server")
    template = service.create_template(
        {"serverId": server["id"], "name": "Train", "command": "python train.py --lr {{lr}}"},
        USER_A,
    )
    history = service.record_history({"serverId": server["id"], "command": "pwd", "source": "terminal"}, USER_A)

    assert template["variables"] == ["lr"]
    assert service.list_templates(server["id"], USER_A)[0]["id"] == template["id"]
    with pytest.raises(ToolboxError):
        service.list_templates(server["id"], USER_B)
    assert service.list_history(USER_A)[0]["id"] == history["id"]
    assert service.list_history(USER_B) == []


def test_due_scheduler_launches_remote_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    server = add_workspace_server(USER_A, name="Screen Server")
    with user_tool_connection_context(USER_A.id, service.TOOL_ID) as conn:
        conn.execute("UPDATE ssh_servers SET has_screen = 1 WHERE id = ?", (server["id"],))
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
    with user_tool_connection_context(USER_A.id, service.TOOL_ID) as conn:
        conn.execute("UPDATE ssh_scheduled_tasks SET next_run_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (task["id"],))

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

    def invoke_shell(self, term: str, width: int = 80, height: int = 24) -> FakeChannel:
        self.term = term
        self.width = width
        self.height = height
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

    assert socket.accepted is True
    assert socket.sent_json[0]["type"] == "error"
    assert socket.close_code == 4401


def test_terminal_websocket_opens_shell_and_resizes(monkeypatch: pytest.MonkeyPatch) -> None:
    user, token = login("admin", "admin123")
    server = add_workspace_server(user, name="WS Auth Test")
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
