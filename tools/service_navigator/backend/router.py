from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field

from backend.app.core.security import require_user
from tools.service_navigator.backend import service

router = APIRouter()


class SitePayload(BaseModel):
    title: str = ""
    slug: str = ""
    description: str = ""


class TargetPayload(BaseModel):
    label: str = ""
    address: str
    customPorts: str = ""


class ServicePayload(BaseModel):
    displayName: str = ""
    category: str = ""
    description: str = ""
    navigationUrl: str = ""
    connectionCommand: str = ""
    visible: bool = True
    sortOrder: int = 0


class VisibilityPayload(BaseModel):
    visibility: str


class AccessUserPayload(BaseModel):
    username: str


class PasswordPayload(BaseModel):
    label: str = ""
    password: str = ""
    enabled: bool = True


@router.get("/site")
def get_site(request: Request) -> dict:
    result = service.get_site(require_user(request))
    return {"site": result}


@router.post("/site", status_code=201)
def create_site(request: Request, payload: SitePayload) -> dict:
    return {"site": service.create_site(payload.model_dump(), require_user(request))}


@router.put("/site")
def update_site(request: Request, payload: SitePayload) -> dict:
    return {"site": service.update_site(payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/site")
def delete_site(request: Request) -> dict[str, bool]:
    service.delete_site(require_user(request))
    return {"deleted": True}


@router.post("/targets", status_code=201)
def add_target(request: Request, payload: TargetPayload) -> dict:
    return {"target": service.add_target(payload.model_dump(), require_user(request))}


@router.get("/targets")
def targets(request: Request) -> dict:
    return {"targets": service.list_targets(require_user(request))}


@router.put("/targets/{target_id}")
def update_target(request: Request, target_id: str, payload: TargetPayload) -> dict:
    return {"target": service.update_target(target_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/targets/{target_id}")
def delete_target(request: Request, target_id: str) -> dict[str, bool]:
    service.delete_target(target_id, require_user(request))
    return {"deleted": True}


@router.post("/scans", status_code=202)
def scan_all(request: Request, tasks: BackgroundTasks) -> dict:
    queued = service.request_scan(require_user(request))
    tasks.add_task(service.run_scan, queued["id"])
    return {"scan": queued}


@router.post("/targets/{target_id}/scans", status_code=202)
def scan_target(request: Request, target_id: str, tasks: BackgroundTasks) -> dict:
    queued = service.request_scan(require_user(request), target_id)
    tasks.add_task(service.run_scan, queued["id"])
    return {"scan": queued}


@router.get("/scans/{run_id}")
def scan(request: Request, run_id: str) -> dict:
    return {"scan": service.get_scan(run_id, require_user(request))}


@router.get("/services")
def services(request: Request) -> dict:
    return {"services": service.list_services(require_user(request))}


@router.put("/services/{service_id}")
def update_service(request: Request, service_id: str, payload: ServicePayload) -> dict:
    return {"service": service.update_service(service_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.get("/access")
def access(request: Request) -> dict:
    return service.get_access_settings(require_user(request))


@router.put("/access")
def visibility(request: Request, payload: VisibilityPayload) -> dict:
    return service.set_visibility(payload.visibility, require_user(request))


@router.post("/access/users")
def add_access_user(request: Request, payload: AccessUserPayload) -> dict:
    return service.add_access_user(payload.username, require_user(request))


@router.delete("/access/users/{user_id}")
def remove_access_user(request: Request, user_id: str) -> dict[str, bool]:
    service.remove_access_user(user_id, require_user(request))
    return {"deleted": True}


@router.post("/access/passwords", status_code=201)
def add_password(request: Request, payload: PasswordPayload) -> dict:
    return {"password": service.add_password(payload.label, payload.password, require_user(request))}


@router.put("/access/passwords/{password_id}")
def update_password(request: Request, password_id: str, payload: PasswordPayload) -> dict:
    return {"password": service.update_password(password_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/access/passwords/{password_id}")
def delete_password(request: Request, password_id: str) -> dict[str, bool]:
    service.delete_password(password_id, require_user(request))
    return {"deleted": True}


from tools.service_navigator.backend.public import mount_extra  # noqa: E402
