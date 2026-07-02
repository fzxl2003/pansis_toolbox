from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from backend.app.core.config import get_settings
from backend.app.core.security import get_optional_user, require_admin, require_user
from backend.app.services.auth_service import (
    change_user_password,
    create_user,
    delete_user,
    ensure_default_user,
    list_users,
    login,
    logout,
    reset_user_password,
    set_user_disabled,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    displayName: str
    password: str
    role: str = "user"


class ResetPasswordRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str


class SetDisabledRequest(BaseModel):
    disabled: bool


@router.post("/auth/login")
def login_route(payload: LoginRequest, response: Response) -> dict:
    user, token = login(payload.username, payload.password)
    response.set_cookie(
        key=get_settings().session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=14 * 24 * 60 * 60,
        path="/",
    )
    return {"authenticated": True, "user": user.public_dict()}


@router.post("/auth/logout")
def logout_route(request: Request, response: Response) -> dict[str, bool]:
    logout(request.cookies.get(get_settings().session_cookie_name))
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"authenticated": False}


@router.get("/auth/me")
def me_route(request: Request) -> dict:
    ensure_default_user()
    user = get_optional_user(request)
    return {"authenticated": user is not None, "user": user.public_dict() if user else None}


@router.get("/auth/users")
def list_users_route(request: Request) -> dict:
    require_admin(request)
    return {"users": [user.public_dict() for user in list_users()]}


@router.get("/auth/users-basic")
def list_users_basic_route(request: Request) -> dict:
    """返回所有已激活用户的基础信息（id/username/displayName），供所有已登录用户查看。
    用于非管理员用户在分配 viewer 时选择目标用户。
    """
    require_user(request)
    users = list_users()
    return {
        "users": [
            {"id": u.id, "username": u.username, "displayName": u.display_name}
            for u in users
            if not u.disabled
        ]
    }


@router.post("/auth/users")
def create_user_route(request: Request, payload: CreateUserRequest) -> dict:
    require_admin(request)
    user = create_user(payload.username, payload.displayName, payload.password, payload.role)
    return {"user": user.public_dict()}


@router.post("/auth/users/{user_id}/password")
def reset_user_password_route(request: Request, user_id: str, payload: ResetPasswordRequest) -> dict:
    require_admin(request)
    user = reset_user_password(user_id, payload.password)
    return {"user": user.public_dict()}


@router.post("/auth/password")
def change_password_route(request: Request, payload: ChangePasswordRequest) -> dict:
    user = require_user(request)
    updated = change_user_password(user, payload.currentPassword, payload.newPassword)
    return {"user": updated.public_dict(), "sessionsRevoked": True}


@router.post("/auth/users/{user_id}/disabled")
def set_user_disabled_route(request: Request, user_id: str, payload: SetDisabledRequest) -> dict:
    require_admin(request)
    user = set_user_disabled(user_id, payload.disabled)
    return {"user": user.public_dict()}


@router.delete("/auth/users/{user_id}")
def delete_user_route(request: Request, user_id: str) -> dict[str, bool]:
    require_admin(request)
    delete_user(user_id)
    return {"deleted": True}


@router.get("/auth/sso/login")
def sso_login() -> dict[str, str]:
    return {"status": "not_configured", "message": "SSO 尚未配置"}


@router.get("/auth/sso/callback")
def sso_callback() -> dict[str, str]:
    return {"status": "not_configured", "message": "SSO 尚未配置"}
