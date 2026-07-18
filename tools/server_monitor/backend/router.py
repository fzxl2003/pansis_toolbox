from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.security import get_optional_user, require_user

from tools.server_monitor.backend.service import (
    collect_snapshot,
    create_server,
    delete_server,
    delete_directory_usage,
    directory_usage,
    history,
    kill_gpu_process,
    list_directory_usages,
    list_servers,
    update_server,
)

router = APIRouter()


class ServerPayload(BaseModel):
    name: str
    host: str
    port: int = 22
    sshUsername: str
    sshPassword: str | None = None
    isDefault: bool = False
    directoryWhitelist: list[str] = Field(default_factory=list)
    directoryRefreshSeconds: int = 300


class CreateServerPayload(ServerPayload):
    sshPassword: str


class DirectoryPayload(BaseModel):
    path: str


class KillProcessPayload(BaseModel):
    pid: int


@router.get("/servers")
def list_servers_route(request: Request) -> dict:
    user = get_optional_user(request)
    return {"servers": list_servers(user)}


@router.post("/servers")
def create_server_route(request: Request, payload: CreateServerPayload) -> dict:
    user = require_user(request)
    return {"server": create_server(payload.model_dump(), user)}


@router.put("/servers/{server_id}")
def update_server_route(request: Request, server_id: str, payload: ServerPayload) -> dict:
    user = require_user(request)
    return {"server": update_server(server_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/servers/{server_id}")
def delete_server_route(request: Request, server_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_server(server_id, user)
    return {"deleted": True}


@router.get("/servers/{server_id}/snapshot")
def snapshot_route(request: Request, server_id: str, force: bool = False) -> dict:
    user = get_optional_user(request)
    return collect_snapshot(server_id, user, force=force)


@router.get("/servers/{server_id}/history")
def history_route(request: Request, server_id: str, hours: int = 24) -> dict:
    user = get_optional_user(request)
    return history(server_id, user, hours=hours)


@router.post("/servers/{server_id}/directories")
def directory_route(request: Request, server_id: str, payload: DirectoryPayload) -> dict:
    user = get_optional_user(request)
    return directory_usage(server_id, payload.path, user)


@router.get("/servers/{server_id}/directories")
def list_directories_route(request: Request, server_id: str) -> dict:
    user = get_optional_user(request)
    return list_directory_usages(server_id, user)


@router.delete("/servers/{server_id}/directories")
def delete_directory_route(request: Request, server_id: str, payload: DirectoryPayload) -> dict[str, bool]:
    user = get_optional_user(request)
    delete_directory_usage(server_id, payload.path, user)
    return {"deleted": True}


@router.post("/servers/{server_id}/processes/kill")
def kill_process_route(request: Request, server_id: str, payload: KillProcessPayload) -> dict:
    user = require_user(request)
    return kill_gpu_process(server_id, payload.pid, user)
