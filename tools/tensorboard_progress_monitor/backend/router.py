from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.security import require_user
from tools.tensorboard_progress_monitor.backend import service
from tools.tensorboard_progress_monitor.backend.tb_proxy import mount_extra

router = APIRouter()


class ServerPayload(BaseModel):
    serverId: str
    tbPythonMode: Literal["conda", "path"] = "conda"
    tbCondaBasePath: str = ""
    tbCondaEnv: str = ""
    tbPythonPath: str = ""


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
    tbExtraParams: list[dict[str, str]] = []
    tbDefaultParams: str = ""


class MoveTaskPayload(BaseModel):
    direction: Literal["up", "down"]


class RemoteYamlPathPayload(BaseModel):
    path: str


class RemoteYamlWritePayload(RemoteYamlPathPayload):
    content: str


@router.get("/servers")
def list_servers(request: Request) -> dict:
    return {"servers": service.list_servers(require_user(request))}


@router.post("/servers")
def create_server(request: Request, payload: ServerPayload) -> dict:
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


@router.get("/servers/{server_id}/conda-envs")
def conda_envs(request: Request, server_id: str) -> dict:
    return service.list_conda_envs(server_id, require_user(request))


@router.post("/servers/{server_id}/check-tb-environment")
def check_tb_environment(request: Request, server_id: str) -> dict:
    return service.check_tb_environment(server_id, require_user(request))


@router.post("/servers/{server_id}/remote-yaml/read")
def read_remote_yaml(request: Request, server_id: str, payload: RemoteYamlPathPayload) -> dict:
    return service.read_remote_yaml(server_id, payload.path, require_user(request))


@router.put("/servers/{server_id}/remote-yaml")
def write_remote_yaml(request: Request, server_id: str, payload: RemoteYamlWritePayload) -> dict:
    return service.write_remote_yaml(server_id, payload.path, payload.content, require_user(request))


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


@router.get("/tasks/{task_id}/tb-session")
def get_tb_session(request: Request, task_id: str) -> dict:
    return service.get_tb_session(task_id, require_user(request))


@router.post("/tasks/{task_id}/tb-session/start")
def start_tb_session(request: Request, task_id: str) -> dict:
    return service.start_tb_session(task_id, require_user(request))


@router.post("/tasks/{task_id}/tb-session/restart")
def restart_tb_session(request: Request, task_id: str) -> dict:
    return service.start_tb_session(task_id, require_user(request), restart=True)


@router.post("/tasks/{task_id}/tb-session/stop")
def stop_tb_session(request: Request, task_id: str) -> dict:
    return service.stop_tb_session(task_id, require_user(request))


@router.post("/tasks/{task_id}/tb-session/check")
def check_tb_session(request: Request, task_id: str) -> dict:
    return service.check_tb_session(task_id, require_user(request))


@router.get("/tasks/{task_id}/tb-url")
def tb_group_url(request: Request, task_id: str, group: str) -> dict:
    return service.tb_group_url(task_id, group, require_user(request))
