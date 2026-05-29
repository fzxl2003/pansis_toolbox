from __future__ import annotations

from backend.app.registry.models import RegisteredTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def replace_all(self, tools: list[RegisteredTool]) -> None:
        self._tools = {tool.tool_id: tool for tool in tools}

    def all(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def get(self, tool_id: str) -> RegisteredTool | None:
        return self._tools.get(tool_id)


tool_registry = ToolRegistry()
