from fastapi import APIRouter

from backend.app.services.tool_service import get_tool, list_tools

router = APIRouter()


@router.get("/tools")
def tools() -> list[dict]:
    return list_tools()


@router.get("/tools/{tool_id}")
def tool_detail(tool_id: str) -> dict:
    return get_tool(tool_id)
