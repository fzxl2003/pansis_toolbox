from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile
from pydantic import BaseModel

from backend.app.core.security import require_user
from tools.service_navigator.backend import service
from tools.service_navigator.backend.public import mount_extra

router = APIRouter()


class SitePayload(BaseModel):
    title: str = ""
    slug: str = ""
    description: str = ""
    theme: str = "auto"
    accentColor: str = "#4f7cff"
    cardOpacity: int = 84
    cardBlur: bool = True
    backgroundOverlayOpacity: int = 50


class TargetPayload(BaseModel):
    label: str = ""
    address: str
    customPorts: str = ""
    showInNavigation: bool = True


class ServicePayload(BaseModel):
    serviceType: str = "port"
    displayName: str = ""
    description: str = ""
    navigationUrl: str = ""
    commandDescription: str = ""
    connectionCommand: str = ""  # Backward-compatible input alias.
    serviceTemplate: str = "generic"
    healthEnabled: bool = True
    healthUrl: str = ""


class HealthSettingsPayload(BaseModel):
    checkIntervalSeconds: int = 300
    emailRecipients: list[str] | str = []
    confirmCount: int = 3
    repeatIntervalSeconds: int = 3600
    maxRepeatCount: int = 0


class VisibilityPayload(BaseModel):
    visibility: str


class AccessUserPayload(BaseModel):
    username: str


class PasswordPayload(BaseModel):
    label: str = ""
    password: str = ""
    enabled: bool = True


class NavigationPagePayload(BaseModel):
    name: str = ""
    visible: bool = True


class NavigationOrderPayload(BaseModel):
    pageIds: list[str] = []


class TargetOrderPayload(BaseModel):
    targetIds: list[str] = []


class NavigationItemPayload(BaseModel):
    pageId: str = ""
    size: str = "small"
    iconId: str = ""


class NavigationIconPayload(BaseModel):
    name: str = ""
    size: str = ""
    iconSource: str = "text"
    faviconServiceId: str = ""
    iconText: str = ""
    iconColor: str = "#4f7cff"
    serviceIds: list[str] = []
    detectedServiceId: str = ""
    destinationType: str = "service"
    externalUrl: str = ""


class NavigationLayoutPayload(BaseModel):
    placements: list[dict] = []


class NavigationCanvasPayload(BaseModel):
    items: list[dict] = []


class BackgroundPayload(BaseModel):
    source: str = "default"


class CatalogVisibilityPayload(BaseModel):
    visible: bool = True


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


@router.put("/targets/order")
def order_targets(request: Request, payload: TargetOrderPayload) -> dict:
    return {"targets": service.reorder_targets(payload.targetIds, require_user(request))}


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


@router.get("/navigation")
def navigation(request: Request) -> dict:
    return {"navigation": service.get_navigation(require_user(request))}


@router.get("/navigation/icons")
def list_navigation_icons(request: Request) -> dict:
    return {"icons": service.list_nav_icons(require_user(request))}


@router.post("/navigation/icons", status_code=201)
def create_navigation_icon(request: Request, payload: NavigationIconPayload) -> dict:
    return {"icon": service.create_nav_icon(payload.model_dump(), require_user(request))}


@router.put("/navigation/icons/{icon_id}")
def update_navigation_icon(request: Request, icon_id: str, payload: NavigationIconPayload) -> dict:
    return {"icon": service.update_nav_icon(icon_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/navigation/icons/{icon_id}")
def delete_navigation_icon(request: Request, icon_id: str) -> dict[str, bool]:
    service.delete_nav_icon(icon_id, require_user(request))
    return {"deleted": True}


@router.post("/navigation/icons/{icon_id}/icon", status_code=201)
async def upload_navigation_icon(request: Request, icon_id: str, file: UploadFile = File(...)) -> dict:
    content = await file.read(service.NAV_ICON_LIMIT + 1)
    return {"icon": service.update_nav_custom_icon(icon_id, file.filename or "icon", content, require_user(request))}


@router.delete("/navigation/icons/{icon_id}/icon")
def delete_navigation_icon_file(request: Request, icon_id: str) -> dict:
    return {"icon": service.clear_nav_custom_icon(icon_id, require_user(request))}


@router.post("/navigation/pages", status_code=201)
def create_navigation_page(request: Request, payload: NavigationPagePayload) -> dict:
    return {"page": service.create_nav_page(payload.model_dump(), require_user(request))}


@router.put("/navigation/pages/order")
def order_navigation_pages(request: Request, payload: NavigationOrderPayload) -> dict:
    return {"navigation": service.reorder_nav_pages(payload.pageIds, require_user(request))}


@router.put("/navigation/pages/{page_id}")
def update_navigation_page(request: Request, page_id: str, payload: NavigationPagePayload) -> dict:
    return {"page": service.update_nav_page(page_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/navigation/pages/{page_id}")
def delete_navigation_page(request: Request, page_id: str) -> dict[str, bool]:
    service.delete_nav_page(page_id, require_user(request))
    return {"deleted": True}


@router.put("/navigation/pages/{page_id}/layouts/{breakpoint}")
def update_navigation_layout(request: Request, page_id: str, breakpoint: int, payload: NavigationLayoutPayload) -> dict:
    return {"navigation": service.save_nav_layout(page_id, breakpoint, payload.placements, require_user(request))}


@router.put("/navigation/pages/{page_id}/canvas")
def update_navigation_canvas(request: Request, page_id: str, payload: NavigationCanvasPayload) -> dict:
    return {"navigation": service.save_nav_canvas(page_id, payload.items, require_user(request))}


@router.post("/navigation/items", status_code=201)
def create_navigation_item(request: Request, payload: NavigationItemPayload) -> dict:
    return {"item": service.create_nav_item(payload.model_dump(), require_user(request))}


@router.put("/navigation/items/{item_id}")
def update_navigation_item(request: Request, item_id: str, payload: NavigationItemPayload) -> dict:
    return {"item": service.update_nav_item(item_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/navigation/items/{item_id}")
def delete_navigation_item(request: Request, item_id: str) -> dict[str, bool]:
    service.delete_nav_item(item_id, require_user(request))
    return {"deleted": True}


@router.put("/background")
def update_background(request: Request, payload: BackgroundPayload) -> dict:
    return {"site": service.update_background_source(payload.source, require_user(request))}


@router.post("/background/upload")
async def upload_background(request: Request, file: UploadFile = File(...)) -> dict:
    content = await file.read(service.BACKGROUND_LIMIT + 1)
    return {"site": service.update_custom_background(file.filename or "background", content, require_user(request))}


@router.put("/catalog-visibility")
def catalog_visibility(request: Request, payload: CatalogVisibilityPayload) -> dict:
    return {"site": service.set_all_services_visible(payload.visible, require_user(request))}


@router.get("/health/settings")
def health_settings(request: Request) -> dict:
    return {"settings": service.get_health_settings(require_user(request))}


@router.put("/health/settings")
def update_health_settings(request: Request, payload: HealthSettingsPayload) -> dict:
    return {"settings": service.update_health_settings(payload.model_dump(exclude_unset=True), require_user(request))}


@router.post("/health/checks")
def check_all_health(request: Request) -> dict:
    return {"checks": service.check_site_health(require_user(request))}


@router.post("/services/{service_id}/health/check")
def check_service_health(request: Request, service_id: str) -> dict:
    return {"check": service.check_health(service_id, require_user(request))}


@router.get("/services/{service_id}/health/snapshots")
def health_snapshots(request: Request, service_id: str, limit: int = 100) -> dict:
    return {"snapshots": service.list_health_snapshots(service_id, require_user(request), limit)}


@router.get("/health/events")
def health_events(request: Request, limit: int = 100) -> dict:
    return {"events": service.list_health_events(require_user(request), limit)}


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
