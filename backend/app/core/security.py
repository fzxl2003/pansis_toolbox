from pathlib import Path

from fastapi import Request
from starlette.requests import HTTPConnection

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User, get_user_by_session_token


def get_optional_user(connection: HTTPConnection) -> User | None:
    token = connection.cookies.get(get_settings().session_cookie_name)
    return get_user_by_session_token(token)


def require_user(request: Request) -> User:
    user = get_optional_user(request)
    if user is None:
        raise ToolboxError(
            "LOGIN_REQUIRED",
            "请先登录后再使用该功能",
            status_code=401,
            extra={"loginUrl": "/login"},
        )
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "需要管理员权限", status_code=403)
    return user


def require_user_tool_data_dir(request: Request, tool_id: str) -> Path:
    user = require_user(request)
    safe_tool_id = tool_id.replace("/", "_").replace("\\", "_")
    path = get_settings().storage_dir / "user_data" / user.id / "tools" / safe_tool_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_current_user_id(request: Request) -> str:
    user = get_optional_user(request)
    return user.id if user else "anonymous"
