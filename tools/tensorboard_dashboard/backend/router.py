"""TensorBoard dashboard API router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.security import require_user

from tools.tensorboard_dashboard.backend.proxy import mount_extra  # re-exported for loader
from tools.tensorboard_dashboard.backend.service import (
    browse_dirs,
    check_python_env,
    check_session,
    create_server,
    delete_server,
    delete_session,
    get_session_url,
    list_conda_envs,
    list_servers,
    list_sessions,
    restart_session,
    start_session,
    stop_session,
    test_server,
    update_server,
    update_session,
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
    condaBasePath: str = ""


class StartSessionPayload(BaseModel):
    serverId: str
    name: str
    logdir: str
    pythonMode: str = "conda"
    condaEnv: str = ""
    pythonPath: str = ""
    extraParams: str = ""


class CheckPythonEnvPayload(BaseModel):
    pythonMode: str = "conda"
    condaEnv: str = ""
    pythonPath: str = ""


class UpdateSessionPayload(BaseModel):
    serverId: str | None = None
    name: str | None = None
    logdir: str | None = None
    pythonMode: str | None = None
    condaEnv: str | None = None
    pythonPath: str | None = None
    extraParams: str | None = None


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


@router.post("/servers/{server_id}/check-python-env")
def check_python_env_route(request: Request, server_id: str, payload: CheckPythonEnvPayload) -> dict[str, Any]:
    user = require_user(request)
    return check_python_env(server_id, payload.model_dump(), user)


@router.get("/servers/{server_id}/conda-envs")
def list_conda_envs_route(request: Request, server_id: str) -> dict[str, Any]:
    user = require_user(request)
    return list_conda_envs(server_id, user)


@router.get("/servers/{server_id}/browse-dirs")
def browse_dirs_route(request: Request, server_id: str, path: str = "/") -> dict[str, Any]:
    user = require_user(request)
    return browse_dirs(server_id, path, user)


# ---- Sessions ----


@router.get("/sessions")
def list_sessions_route(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"sessions": list_sessions(user)}


@router.post("/sessions")
def start_session_route(request: Request, payload: StartSessionPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"session": start_session(payload.model_dump(), user)}


@router.put("/sessions/{session_id}")
def update_session_route(request: Request, session_id: str, payload: UpdateSessionPayload) -> dict[str, Any]:
    user = require_user(request)
    return {"session": update_session(session_id, payload.model_dump(exclude_unset=True), user)}


@router.post("/sessions/{session_id}/restart")
def restart_session_route(request: Request, session_id: str) -> dict[str, Any]:
    user = require_user(request)
    return {"session": restart_session(session_id, user)}


@router.post("/sessions/{session_id}/stop")
def stop_session_route(request: Request, session_id: str) -> dict[str, Any]:
    user = require_user(request)
    return {"session": stop_session(session_id, user)}


@router.post("/sessions/{session_id}/check")
def check_session_route(request: Request, session_id: str) -> dict[str, Any]:
    user = require_user(request)
    return {"session": check_session(session_id, user)}


@router.delete("/sessions/{session_id}")
def delete_session_route(request: Request, session_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_session(session_id, user)
    return {"deleted": True}


@router.get("/sessions/{session_id}/url")
def get_session_url_route(request: Request, session_id: str) -> dict[str, str]:
    user = require_user(request)
    return get_session_url(session_id, user)
