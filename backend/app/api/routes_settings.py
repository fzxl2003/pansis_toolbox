from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import require_admin
from backend.app.services.email_service import get_email_config, save_email_config, send_email

router = APIRouter()


class EmailConfigPayload(BaseModel):
    smtpHost: str = ""
    smtpPort: int = 465
    smtpUsername: str = ""
    smtpPassword: str = ""
    smtpFromAddress: str = ""
    smtpFromName: str = "实验监控系统"


@router.get("/settings/email-config")
def get_email_config_route(request: Request) -> dict:
    """Get platform email configuration. Admin only."""
    require_admin(request)
    return get_email_config()


@router.post("/settings/email-config")
def save_email_config_route(request: Request, payload: EmailConfigPayload) -> dict:
    """Save platform email configuration. Admin only."""
    require_admin(request)
    return save_email_config(payload.model_dump())


@router.post("/settings/email-config/test")
def test_email_config_route(request: Request, payload: EmailConfigPayload) -> dict:
    """Test platform email configuration by sending a test email. Admin only."""
    require_admin(request)
    # Save config first so that send_email uses the latest settings
    save_email_config(payload.model_dump())
    test_to = payload.smtpFromAddress or payload.smtpUsername
    if not test_to:
        raise ToolboxError("INVALID_INPUT", "需要提供发件人地址作为测试收件人", status_code=400)
    try:
        send_email(
            [test_to],
            "Pansis Toolbox - 邮件配置测试",
            "这是一封测试邮件，如果您收到此邮件，说明平台邮件配置正确。\n\n此邮件由 Pansis Toolbox 自动发送。",
        )
    except Exception as exc:
        raise ToolboxError("EMAIL_SEND_FAILED", f"邮件发送失败: {exc}", status_code=500) from exc

    return {"success": True, "testTo": test_to}
