from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, WebSocket
from pydantic import BaseModel, Field

from backend.app.core.security import require_user

from tools.ssh_workspace.backend.service import (
    copy_server,
    create_scheduled_task,
    create_screen_session,
    create_server,
    create_template,
    delete_scheduled_task,
    delete_screen_session,
    delete_server,
    delete_template,
    list_history,
    list_scheduled_tasks,
    list_screen_sessions,
    list_servers,
    list_task_runs,
    list_templates,
    list_terminal_tabs,
    record_history,
    rename_screen_session,
    save_terminal_tabs,
    terminal_websocket,
    test_server,
    update_scheduled_task,
    update_server,
    update_template,
)

router = APIRouter()


# ---- Payloads ----


class ServerPayload(BaseModel):
    name: str
    host: str
    port: int = 22
    sshUsername: str
    authType: str = "password"
    sshPassword: str | None = None
    privateKey: str | None = None
    privateKeyPassphrase: str | None = None


class CopyServerPayload(BaseModel):
    name: str | None = None


class ScreenSessionPayload(BaseModel):
    name: str
    command: str = ""


class RenameSessionPayload(BaseModel):
    name: str


class HistoryPayload(BaseModel):
    serverId: str | None = None
    source: str = "terminal"
    command: str
    exitStatus: int | None = None
    screenSession: str | None = None


class TemplatePayload(BaseModel):
    serverId: str
    name: str
    command: str
    description: str = ""
    variables: list[str] = Field(default_factory=list)


class UpdateTemplatePayload(BaseModel):
    name: str | None = None
    command: str | None = None
    description: str | None = None
    variables: list[str] | None = None


class ScheduledTaskPayload(BaseModel):
    serverId: str
    name: str
    command: str
    intervalSeconds: int
    screenNamePrefix: str = ""
    enabled: bool = True


class UpdateScheduledTaskPayload(BaseModel):
    serverId: str | None = None
    name: str | None = None
    command: str | None = None
    intervalSeconds: int | None = None
    screenNamePrefix: str | None = None
    enabled: bool | None = None


class TerminalTabPayload(BaseModel):
    id: str | None = None
    serverId: str
    mode: str = "native"
    screenSession: str = ""
    label: str = ""
    initialCommand: str | None = None


class SaveTabsPayload(BaseModel):
    tabs: list[TerminalTabPayload]


# Resolve forward references (needed because of `from __future__ import annotations`)
TerminalTabPayload.model_rebuild()
SaveTabsPayload.model_rebuild()


# ---- Servers ----


@router.get("/servers")
def list_servers_route(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"servers": list_servers(user)}


@router.post("/servers")
def create_server_route(request: Request, payload: ServerPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"server": create_server(payload.model_dump(), user)}


@router.put("/servers/{server_id}")
def update_server_route(request: Request, server_id: str, payload: ServerPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"server": update_server(server_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/servers/{server_id}")
def delete_server_route(request: Request, server_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_server(server_id, user)
    return {"deleted": True}


@router.post("/servers/{server_id}/test")
def test_server_route(request: Request, server_id: str) -> dict[str, Any]:
    user = require_user(request)
    return test_server(server_id, user)


@router.post("/servers/{server_id}/copy")
def copy_server_route(request: Request, server_id: str, payload: CopyServerPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"server": copy_server(server_id, payload.model_dump(), user)}


# ---- Screen sessions ----


@router.get("/servers/{server_id}/screen/sessions")
def list_screen_sessions_route(request: Request, server_id: str, refresh: bool = True) -> dict[str, Any]:
    user = require_user(request)
    return {"sessions": list_screen_sessions(server_id, user, refresh=refresh)}


@router.post("/servers/{server_id}/screen/sessions")
def create_screen_session_route(request: Request, server_id: str, payload: ScreenSessionPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"session": create_screen_session(server_id, payload.model_dump(), user)}


@router.put("/servers/{server_id}/screen/sessions/{session_name}")
def rename_screen_session_route(request: Request, server_id: str, session_name: str, payload: RenameSessionPayload) -> dict[str, Any]:
    user = require_user(request)
    return rename_screen_session(server_id, session_name, payload.model_dump(), user)


@router.delete("/servers/{server_id}/screen/sessions/{session_name}")
def delete_screen_session_route(request: Request, server_id: str, session_name: str) -> dict[str, bool]:
    user = require_user(request)
    delete_screen_session(server_id, session_name, user)
    return {"deleted": True}


# ---- History ----


@router.get("/history")
def list_history_route(request: Request, serverId: str | None = None, limit: int = 100) -> dict[str, Any]:
    user = require_user(request)
    return {"history": list_history(user, server_id=serverId, limit=limit)}


@router.post("/history")
def record_history_route(request: Request, payload: HistoryPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"history": record_history(payload.model_dump(), user)}


# ---- Templates (server-scoped) ----


@router.get("/servers/{server_id}/templates")
def list_templates_route(request: Request, server_id: str) -> dict[str, Any]:
    user = require_user(request)
    return {"templates": list_templates(server_id, user)}


@router.post("/templates")
def create_template_route(request: Request, payload: TemplatePayload) -> dict[str, Any]:
    user = require_user(request)
    return {"template": create_template(payload.model_dump(), user)}


@router.put("/templates/{template_id}")
def update_template_route(request: Request, template_id: str, payload: UpdateTemplatePayload) -> dict[str, Any]:
    user = require_user(request)
    return {"template": update_template(template_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/templates/{template_id}")
def delete_template_route(request: Request, template_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_template(template_id, user)
    return {"deleted": True}


# ---- Scheduled tasks (server-scoped) ----


@router.get("/scheduled-tasks")
def list_scheduled_tasks_route(request: Request, serverId: str | None = None) -> dict[str, Any]:
    user = require_user(request)
    return {"tasks": list_scheduled_tasks(user, server_id=serverId)}


@router.post("/scheduled-tasks")
def create_scheduled_task_route(request: Request, payload: ScheduledTaskPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"task": create_scheduled_task(payload.model_dump(), user)}


@router.put("/scheduled-tasks/{task_id}")
def update_scheduled_task_route(request: Request, task_id: str, payload: UpdateScheduledTaskPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"task": update_scheduled_task(task_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/scheduled-tasks/{task_id}")
def delete_scheduled_task_route(request: Request, task_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_scheduled_task(task_id, user)
    return {"deleted": True}


@router.get("/scheduled-tasks/{task_id}/runs")
def list_task_runs_route(request: Request, task_id: str, limit: int = 50) -> dict[str, Any]:
    user = require_user(request)
    return {"runs": list_task_runs(task_id, user, limit=limit)}


# ---- Terminal tabs persistence ----


@router.get("/terminal-tabs")
def list_terminal_tabs_route(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"tabs": list_terminal_tabs(user)}


@router.put("/terminal-tabs")
def save_terminal_tabs_route(request: Request, payload: SaveTabsPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"tabs": save_terminal_tabs([t.model_dump() for t in payload.tabs], user)}


# ---- Terminal WebSocket ----


@router.websocket("/ws/terminal")
async def terminal_ws_route(
    websocket: WebSocket,
    serverId: str,
    mode: str = "native",
    screenSession: str | None = None,
    cols: int = 80,
    rows: int = 24,
    initialCommand: str | None = None,
) -> None:
    await terminal_websocket(websocket, serverId, mode=mode, screen_session=screenSession, cols=cols, rows=rows, initial_command=initialCommand)
