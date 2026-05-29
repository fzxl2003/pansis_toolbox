from __future__ import annotations

from backend.app.core.errors import ToolboxError
from backend.app.registry.tool_registry import tool_registry


def list_tools() -> list[dict]:
    return [tool.public_dict() for tool in tool_registry.all()]


def get_tool(tool_id: str) -> dict:
    tool = tool_registry.get(tool_id)
    if tool is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    return tool.public_dict()
