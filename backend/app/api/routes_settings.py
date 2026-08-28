from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_admin, require_user
from backend.app.registry.tool_registry import tool_registry
from backend.app.services.data_management import (
    compute_before_date,
    compute_date_bounds,
    count_data,
    delete_data,
    get_data_usage_by_category,
    get_tool_categories,
)
from backend.app.services.email_service import get_email_config, save_email_config, send_email
from backend.app.services import ssh_server_service

# Project-root relative file that holds the "About" information shown in the
# settings page.  Edit this file to update version / developer / contact info
# without touching code.
ABOUT_FILE = Path("about.json")

router = APIRouter()


class EmailConfigPayload(BaseModel):
    smtpHost: str = ""
    smtpPort: int = 465
    smtpUsername: str = ""
    smtpPassword: str = ""
    smtpFromAddress: str = ""
    smtpFromName: str = "实验监控系统"


class SshServerPayload(BaseModel):
    name: str
    host: str
    port: int = 22
    sshUsername: str
    authType: str = "password"
    sshPassword: str = ""
    privateKey: str = ""
    privateKeyPassphrase: str = ""
    isPublic: bool = False
    allowedUserIds: list[str] = []


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
# Platform SSH servers
# ============================================================

@router.get("/settings/ssh-servers")
def list_ssh_servers_route(request: Request) -> dict:
    """All servers usable by the caller; credentials are never serialized."""
    return {"servers": ssh_server_service.list_servers(require_user(request), include_disabled=False)}


@router.post("/settings/ssh-servers")
def create_ssh_server_route(request: Request, payload: SshServerPayload) -> dict:
    return {"server": ssh_server_service.create_server(payload.model_dump(), require_user(request))}


@router.put("/settings/ssh-servers/{server_id}")
def update_ssh_server_route(request: Request, server_id: str, payload: SshServerPayload) -> dict:
    return {"server": ssh_server_service.update_server(server_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/settings/ssh-servers/{server_id}")
def delete_ssh_server_route(request: Request, server_id: str) -> dict:
    ssh_server_service.delete_server(server_id, require_user(request))
    return {"deleted": True}


@router.post("/settings/ssh-servers/{server_id}/test")
def test_ssh_server_route(request: Request, server_id: str) -> dict:
    return ssh_server_service.test_connection(server_id, require_user(request))


# ============================================================
# Data category & deletion APIs
# ============================================================

class DataDeletionPayload(BaseModel):
    toolId: str
    category: str | None = None  # None = all categories
    beforeDays: int | None = None  # Legacy: delete data older than N days
    startDate: str | None = None  # YYYY-MM-DD inclusive (day-granularity range)
    endDate: str | None = None  # YYYY-MM-DD inclusive
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
def data_usage_route(request: Request, tool_id: str, userId: str | None = None) -> dict:
    """Get per-category data usage for a specific tool.

    Regular users can only query their own data (``userId`` is ignored).
    Admins can pass ``userId`` to target a specific user, or leave it unset
    to aggregate across all users.
    """
    user = require_user(request)
    if tool_registry.get(tool_id) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=tool_id)

    target_user_id: str | None
    if userId is not None:
        if user.role != "admin":
            raise ToolboxError("FORBIDDEN", "只能查看自己的数据", status_code=403)
        target_user_id = userId
    elif user.role == "admin":
        target_user_id = None
    else:
        target_user_id = user.id

    usage = get_data_usage_by_category(tool_id, target_user_id)
    return {"toolId": tool_id, "userId": target_user_id or "", "categories": usage}


class DataCountItem(BaseModel):
    category: str | None = None  # None = all time-based categories
    startDate: str | None = None  # YYYY-MM-DD inclusive
    endDate: str | None = None  # YYYY-MM-DD inclusive


class DataCountPayload(BaseModel):
    toolId: str
    items: list[DataCountItem]
    userId: str | None = None  # Admin-only: target a specific user


@router.post("/settings/data-count")
def data_count_route(request: Request, payload: DataCountPayload) -> dict:
    """Preview the number of rows that would be deleted for each category+range.

    Returns ``{"toolId": ..., "counts": {category: rowCount}}`` so the UI can
    show "selected count" alongside the total count when a date range is chosen.
    """
    user = require_user(request)
    if tool_registry.get(payload.toolId) is None:
        raise ToolboxError("TOOL_NOT_FOUND", "工具不存在", status_code=404, tool_id=payload.toolId)

    target_user_id: str | None
    if payload.userId is not None:
        if user.role != "admin":
            raise ToolboxError("FORBIDDEN", "只能查看自己的数据", status_code=403)
        target_user_id = payload.userId
    elif user.role == "admin":
        target_user_id = None
    else:
        target_user_id = user.id

    counts: dict[str, int] = {}
    for item in payload.items:
        after_date, before_date = compute_date_bounds(item.startDate, item.endDate)
        key = item.category if item.category is not None else "__all__"
        counts[key] = count_data(
            tool_id=payload.toolId,
            user_id=target_user_id,
            category=item.category,
            before_date=before_date,
            after_date=after_date,
        )
    return {"toolId": payload.toolId, "counts": counts}


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

    before_date: str | None
    after_date: str | None
    if payload.startDate or payload.endDate:
        # Day-granularity date range takes precedence over legacy beforeDays.
        after_date, before_date = compute_date_bounds(payload.startDate, payload.endDate)
    else:
        after_date = None
        before_date = compute_before_date(payload.beforeDays)
    return delete_data(
        tool_id=payload.toolId,
        user_id=target_user_id,
        category=payload.category,
        before_date=before_date,
        after_date=after_date,
    )


# ============================================================
# About info
# ============================================================

@router.get("/settings/about")
def about_route(request: Request) -> dict:
    """Return platform about info (version, developer, contact, ...).

    The content is read from ``about.json`` at the project root so it can be
    updated without restarting the service.  Available to any visitor.
    """
    get_optional_user(request)
    if ABOUT_FILE.exists():
        try:
            return json.loads(ABOUT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"items": []}
