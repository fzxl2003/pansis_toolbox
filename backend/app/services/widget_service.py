from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.registry.models import RegisteredWidget
from backend.app.registry.widget_registry import widget_registry
from backend.app.services.auth_service import User
from backend.app.services.tool_access_service import require_tool_access


def list_widgets(user: User | None = None) -> list[dict[str, Any]]:
    return [widget.public_dict() for widget in widget_registry.all() if _can_access_widget(widget, user)]


def get_widget_data(widget_id: str, user: User | None = None) -> dict[str, Any]:
    widget = widget_registry.get(widget_id)
    if widget is None:
        raise ToolboxError("WIDGET_NOT_FOUND", "小组件不存在", status_code=404)
    require_tool_access(widget.tool_id, user)
    if widget.backend_path is not None:
        return _load_widget_data(widget)
    return _placeholder_widget_data(widget)


def get_widget_layout() -> dict[str, Any]:
    path = get_settings().widget_layout_path
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "userId": "local",
        "widgets": [
            {
                "id": widget.widget_id,
                "x": 0,
                "y": index,
                "w": widget.manifest.defaultSize.w,
                "h": widget.manifest.defaultSize.h,
                "enabled": True,
            }
            for index, widget in enumerate(widget_registry.all())
        ],
    }


def save_widget_layout(layout: dict[str, Any]) -> dict[str, Any]:
    path = get_settings().widget_layout_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    return layout


def _load_widget_data(widget: RegisteredWidget) -> dict[str, Any]:
    assert widget.backend_path is not None
    module_name = f"toolbox_widgets.{widget.widget_id.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, widget.backend_path)
    if spec is None or spec.loader is None:
        raise ToolboxError("WIDGET_LOAD_FAILED", "小组件加载失败", status_code=500, tool_id=widget.tool_id)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = getattr(module, "get_widget_data", None)
    if provider is None:
        return _placeholder_widget_data(widget)
    return provider(widget.widget_id)


def _placeholder_widget_data(widget: RegisteredWidget) -> dict[str, Any]:
    return {
        "widgetId": widget.widget_id,
        "type": widget.manifest.type,
        "title": widget.manifest.name,
        "data": {"status": widget.tool_status.value},
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _can_access_widget(widget: RegisteredWidget, user: User | None) -> bool:
    try:
        require_tool_access(widget.tool_id, user)
        return True
    except ToolboxError:
        return False
