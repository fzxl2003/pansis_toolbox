from __future__ import annotations

from backend.app.core.errors import ToolboxError
from backend.app.registry.tool_registry import tool_registry
from backend.app.services.auth_service import User
from backend.app.services.tool_access_service import require_tool_access, visible_tools


def list_tools(user: User | None = None) -> list[dict]:
    return [tool.public_dict() for tool in visible_tools(tool_registry.all(), user)]


def get_tool(tool_id: str, user: User | None = None) -> dict:
    tool = tool_registry.get(tool_id)
    if tool is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    require_tool_access(tool_id, user)
    return tool.public_dict()
