from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from backend.app.core.config import get_settings
from backend.app.core.security import get_optional_user
from backend.app.services.auth_service import ensure_default_user, login, logout

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


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


@router.get("/auth/sso/login")
def sso_login() -> dict[str, str]:
    return {"status": "not_configured", "message": "SSO 尚未配置"}


@router.get("/auth/sso/callback")
def sso_callback() -> dict[str, str]:
    return {"status": "not_configured", "message": "SSO 尚未配置"}
