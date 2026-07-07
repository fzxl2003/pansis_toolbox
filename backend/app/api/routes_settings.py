from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_admin, require_user
from backend.app.registry.tool_registry import tool_registry
from backend.app.services.data_management import (
    compute_before_date,
    delete_data,
    get_data_usage_by_category,
    get_tool_categories,
)
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


# ============================================================
# Data category & deletion APIs
# ============================================================

class DataDeletionPayload(BaseModel):
    toolId: str
    category: str | None = None  # None = all categories
    beforeDays: int | None = None  # Delete data older than N days; None = no time filter
    userId: str | None = None  # Admin-only: target a specific user; None = self (or all users for platform_db)


@router.get("/settings/data-categories")
def data_categories_route(request: Request) -> dict:
    """List all tools and their registered data categories.

    Regular users see categories for tools they can access.  Admins see all.
    """
    user = get_optional_user(request)
    tools = tool_registry.all()
    result = []
    for tool in tools:
        cats = get_tool_categories(tool.tool_id)
        if not cats:
            continue
        result.append({
            "toolId": tool.tool_id,
            "toolName": tool.manifest.name,
            "categories": [
                {
                    "name": c.name,
                    "description": c.description,
                    "timeColumn": c.time_column,
                    "storage": c.storage,
                    "tables": c.tables,
                }
                for c in cats
            ],
        })
    return {"tools": result}


@router.get("/settings/data-usage/{tool_id}")
def data_usage_route(request: Request, tool_id: str) -> dict:
    """Get per-category data usage for the current user in a specific tool."""
    user = require_user(request)
    if tool_registry.get(tool_id) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)
    usage = get_data_usage_by_category(tool_id, user.id)
    return {"toolId": tool_id, "userId": user.id, "categories": usage}


@router.delete("/settings/data")
def delete_data_route(request: Request, payload: DataDeletionPayload) -> dict:
    """Delete data by tool / category / time-range.

    Regular users can only delete their own data.  Admins can specify a
    ``userId`` to target another user, or leave it unset to delete across all
    users (for user_tool_db storage) or all shared data (for platform_db).
    """
    user = require_user(request)
    if tool_registry.get(payload.toolId) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=payload.toolId)

    target_user_id: str | None
    if payload.userId is not None:
        # Admin targeting a specific user
        if user.role != "admin":
            raise ToolboxError("FORBIDDEN", "只能删除自己的数据", status_code=403)
        target_user_id = payload.userId
    elif user.role == "admin":
        # Admin deleting across all users (for user_tool_db) or all shared
        # data (for platform_db).  Works with or without a category filter.
        target_user_id = None
    else:
        # Self-service: non-admin can only delete their own data
        target_user_id = user.id

    before_date = compute_before_date(payload.beforeDays)
    return delete_data(
        tool_id=payload.toolId,
        user_id=target_user_id,
        category=payload.category,
        before_date=before_date,
    )
