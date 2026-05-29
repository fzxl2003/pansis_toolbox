from __future__ import annotations

from backend.app.registry.models import RegisteredTool, RegisteredWidget, ToolStatus


class WidgetRegistry:
    def __init__(self) -> None:
        self._widgets: dict[str, RegisteredWidget] = {}

    def rebuild_from_tools(self, tools: list[RegisteredTool]) -> None:
        widgets: dict[str, RegisteredWidget] = {}
        for tool in tools:
            for widget in tool.manifest.widgets:
                widget_id = f"{tool.tool_id}.{widget.id}"
                backend_path = None
                if widget.backend:
                    candidate = tool.root_path / widget.backend
                    if candidate.exists():
                        backend_path = candidate
                widgets[widget_id] = RegisteredWidget(
                    widget_id=widget_id,
                    tool_id=tool.tool_id,
                    manifest=widget,
                    tool_status=tool.status if tool.status != ToolStatus.available else ToolStatus.available,
                    backend_path=backend_path,
                )
        self._widgets = widgets

    def all(self) -> list[RegisteredWidget]:
        return list(self._widgets.values())

    def get(self, widget_id: str) -> RegisteredWidget | None:
        return self._widgets.get(widget_id)


widget_registry = WidgetRegistry()
