from typing import Any

from fastapi import APIRouter, Request

from backend.app.core.security import get_optional_user
from backend.app.services.widget_service import (
    get_widget_data,
    get_widget_layout,
    list_widgets,
    save_widget_layout,
)

router = APIRouter()


@router.get("/widgets")
def widgets(request: Request) -> list[dict[str, Any]]:
    return list_widgets(get_optional_user(request))


@router.get("/widgets/layout")
def widget_layout() -> dict[str, Any]:
    return get_widget_layout()


@router.put("/widgets/layout")
def update_widget_layout(layout: dict[str, Any]) -> dict[str, Any]:
    return save_widget_layout(layout)


@router.get("/widgets/{widget_id}/data")
def widget_data(request: Request, widget_id: str) -> dict[str, Any]:
    return get_widget_data(widget_id, get_optional_user(request))
