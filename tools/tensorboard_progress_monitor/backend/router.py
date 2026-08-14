from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.security import require_user
from tools.tensorboard_progress_monitor.backend import service

router = APIRouter()


class ServerPayload(BaseModel):
    name: str
    host: str
    port: int = 22
    sshUsername: str
    sshPassword: str = ""


class CreateServerPayload(ServerPayload):
    sshPassword: str


class TaskPayload(BaseModel):
    name: str
    serverId: str
    configSource: Literal["inline", "remote_yaml"] = "inline"
    inlineYaml: str = ""
    remoteYamlPath: str = ""
    pythonCommand: str = "python3"
    reportIntervalSeconds: int = Field(default=60, ge=30, le=3600)
    enabled: bool = True
    showInTabs: bool = True


class MoveTaskPayload(BaseModel):
    direction: Literal["up", "down"]


@router.get("/servers")
def list_servers(request: Request) -> dict:
    return {"servers": service.list_servers(require_user(request))}


@router.post("/servers")
def create_server(request: Request, payload: CreateServerPayload) -> dict:
    return {"server": service.create_server(payload.model_dump(), require_user(request))}


@router.put("/servers/{server_id}")
def update_server(request: Request, server_id: str, payload: ServerPayload) -> dict:
    return {"server": service.update_server(server_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/servers/{server_id}")
def delete_server(request: Request, server_id: str) -> dict:
    service.delete_server(server_id, require_user(request))
    return {"deleted": True}


@router.post("/servers/{server_id}/test")
def test_server(request: Request, server_id: str) -> dict:
    return service.test_server(server_id, require_user(request))


@router.get("/tasks")
def list_tasks(request: Request) -> dict:
    return {"tasks": service.list_tasks(require_user(request))}


@router.post("/tasks")
def create_task(request: Request, payload: TaskPayload) -> dict:
    return {"task": service.create_task(payload.model_dump(), require_user(request))}


@router.put("/tasks/{task_id}")
def update_task(request: Request, task_id: str, payload: TaskPayload) -> dict:
    return {"task": service.update_task(task_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/tasks/{task_id}")
def delete_task(request: Request, task_id: str) -> dict:
    service.delete_task(task_id, require_user(request))
    return {"deleted": True}


@router.post("/tasks/{task_id}/copy")
def copy_task(request: Request, task_id: str) -> dict:
    return {"task": service.copy_task(task_id, require_user(request))}


@router.post("/tasks/{task_id}/move")
def move_task(request: Request, task_id: str, payload: MoveTaskPayload) -> dict:
    return {"task": service.move_task(task_id, payload.direction, require_user(request))}


@router.post("/tasks/{task_id}/validate")
def validate_task(request: Request, task_id: str) -> dict:
    return service.validate_task_config(task_id, require_user(request))


@router.post("/tasks/{task_id}/refresh")
def refresh_task(request: Request, task_id: str) -> dict:
    return service.refresh_task(task_id, require_user(request))


@router.get("/tasks/{task_id}/report")
def get_report(request: Request, task_id: str) -> dict:
    return service.get_latest_report(task_id, require_user(request))


@router.get("/tasks/{task_id}/history")
def get_history(request: Request, task_id: str, limit: int = 30) -> dict:
    return service.get_history(task_id, require_user(request), limit=limit)
