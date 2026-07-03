from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_admin, require_user
from backend.app.registry.tool_registry import tool_registry
from backend.app.services.tool_access_service import (
    clear_tool_storage,
    clear_user_storage,
    clear_user_tool_storage,
    get_storage_usage,
    get_user_storage_usage,
    list_tool_access,
    update_tool_access,
)
from backend.app.services.tool_service import get_tool, list_tools

router = APIRouter()


class ToolAccessPayload(BaseModel):
    globalPublic: bool
    allowedUserIds: list[str] = []


# ── Public / user-scoped tool routes ──────────────────────────────────────
# NOTE: static paths like "/tools/my-storage" MUST be declared before the
# parameterised "/tools/{tool_id}" route, otherwise FastAPI matches the
# literal segment as a tool_id.

@router.get("/tools")
def tools(request: Request) -> list[dict]:
    return list_tools(get_optional_user(request))


@router.get("/tools/my-storage")
def my_storage_route(request: Request) -> dict:
    user = require_user(request)
    return get_user_storage_usage(user, tool_registry.all())


@router.delete("/tools/my-storage")
def clear_my_storage_route(request: Request) -> dict:
    user = require_user(request)
    return clear_user_storage(user.id)


@router.delete("/tools/my-storage/{tool_id}")
def clear_my_tool_storage_route(request: Request, tool_id: str) -> dict:
    user = require_user(request)
    if tool_registry.get(tool_id) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return clear_user_tool_storage(tool_id, user.id)


@router.get("/tools/{tool_id}")
def tool_detail(request: Request, tool_id: str) -> dict:
    return get_tool(tool_id, get_optional_user(request))


# ── Admin tool-management routes ──────────────────────────────────────────

@router.get("/tools-admin/access")
def tools_access(request: Request) -> dict:
    require_admin(request)
    return {"items": list_tool_access(tool_registry.all())}


@router.post("/tools-admin/{tool_id}/access")
def save_tool_access(request: Request, tool_id: str, payload: ToolAccessPayload) -> dict:
    require_admin(request)
    if tool_registry.get(tool_id) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return update_tool_access(tool_id, payload.globalPublic, payload.allowedUserIds)


@router.get("/tools-admin/storage-usage")
def storage_usage_route(request: Request) -> dict:
    require_admin(request)
    return get_storage_usage(tool_registry.all())


@router.delete("/tools-admin/users/{user_id}/storage")
def clear_user_storage_route(request: Request, user_id: str) -> dict:
    require_admin(request)
    return clear_user_storage(user_id)


@router.delete("/tools-admin/{tool_id}/storage")
def clear_tool_storage_route(request: Request, tool_id: str) -> dict:
    require_admin(request)
    tool = tool_registry.get(tool_id)
    if tool is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return clear_tool_storage(tool)


@router.delete("/tools-admin/{tool_id}/users/{user_id}/storage")
def clear_user_tool_storage_route(request: Request, tool_id: str, user_id: str) -> dict:
    require_admin(request)
    if tool_registry.get(tool_id) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return clear_user_tool_storage(tool_id, user_id)
