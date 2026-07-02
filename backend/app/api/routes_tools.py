from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_admin
from backend.app.registry.tool_registry import tool_registry
from backend.app.services.tool_access_service import clear_tool_storage, list_tool_access, update_tool_access
from backend.app.services.tool_service import get_tool, list_tools

router = APIRouter()


class ToolAccessPayload(BaseModel):
    globalPublic: bool
    allowedUserIds: list[str] = []


@router.get("/tools")
def tools(request: Request) -> list[dict]:
    return list_tools(get_optional_user(request))


@router.get("/tools/{tool_id}")
def tool_detail(request: Request, tool_id: str) -> dict:
    return get_tool(tool_id, get_optional_user(request))


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


@router.delete("/tools-admin/{tool_id}/storage")
def clear_tool_storage_route(request: Request, tool_id: str) -> dict:
    require_admin(request)
    tool = tool_registry.get(tool_id)
    if tool is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return clear_tool_storage(tool)
