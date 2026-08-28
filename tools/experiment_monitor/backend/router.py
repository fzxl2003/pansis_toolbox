from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.security import get_optional_user, require_admin, require_user

from tools.experiment_monitor.backend.service import (
    add_queue_item,
    collect_due_checks,
    create_alert_action,
    create_monitor_task,
    create_script_group,
    create_server,
    delete_alert_action,
    delete_history_item,
    delete_monitor_task,
    delete_queue_item,
    delete_script_group,
    delete_server,
    get_alert_state,
    get_task_history,
    list_alert_actions,
    list_monitor_tasks,
    list_script_groups,
    list_servers,
    preview_process_filter,
    refresh_screen_sessions,
    reorder_queue,
    reset_alert_state,
    restore_history_to_queue,
    run_monitor_check,
    test_ssh_connection,
    update_alert_action,
    update_monitor_task,
    update_script_group,
    update_server,
)

router = APIRouter()


# ============================================================
# Server (SSH Connection) APIs
# ============================================================

class ServerPayload(BaseModel):
    serverId: str


class CreateServerPayload(ServerPayload):
    pass


@router.get("/servers")
def list_servers_route(request: Request) -> dict:
    user = require_user(request)
    return {"servers": list_servers(user)}


@router.post("/servers")
def create_server_route(request: Request, payload: CreateServerPayload) -> dict:
    user = require_user(request)
    return {"server": create_server(payload.model_dump(), user)}


@router.put("/servers/{server_id}")
def update_server_route(request: Request, server_id: str, payload: ServerPayload) -> dict:
    user = require_user(request)
    return {"server": update_server(server_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/servers/{server_id}")
def delete_server_route(request: Request, server_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_server(server_id, user)
    return {"deleted": True}


@router.post("/servers/{server_id}/test")
def test_ssh_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    return test_ssh_connection(server_id, user)


# ============================================================
# Monitor Task APIs
# ============================================================

class MonitorTaskPayload(BaseModel):
    serverId: str
    name: str
    description: str = ""
    matchMode: str = "simple"  # simple | regex
    matchPattern: str = ""
    filterUser: str = ""
    alertCondition: str = "below"  # below | above | changed
    alertThreshold: int = 0
    alertChangeAmount: int = 1
    confirmCount: int = 3
    checkIntervalSeconds: int = 30
    repeatIntervalSeconds: int = 0  # 重复报警冷却时间（秒），0 = 不限制
    maxRepeatCount: int = 0  # 最多重复报警次数，0 = 不限制
    enabled: bool = True


@router.get("/tasks")
def list_tasks_route(request: Request, serverId: str | None = None) -> dict:
    user = require_user(request)
    return {"tasks": list_monitor_tasks(user, server_id=serverId)}


@router.post("/tasks")
def create_task_route(request: Request, payload: MonitorTaskPayload) -> dict:
    user = require_user(request)
    return {"task": create_monitor_task(payload.model_dump(), user)}


@router.put("/tasks/{task_id}")
def update_task_route(request: Request, task_id: str, payload: MonitorTaskPayload) -> dict:
    user = require_user(request)
    return {"task": update_monitor_task(task_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/tasks/{task_id}")
def delete_task_route(request: Request, task_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_monitor_task(task_id, user)
    return {"deleted": True}


@router.get("/tasks/{task_id}/history")
def task_history_route(request: Request, task_id: str, hours: int = 24) -> dict:
    user = require_user(request)
    return get_task_history(task_id, user, hours=hours)


@router.get("/tasks/{task_id}/alert-state")
def alert_state_route(request: Request, task_id: str) -> dict:
    user = require_user(request)
    state = get_alert_state(task_id, user)
    return {"alertState": state}


@router.post("/tasks/{task_id}/reset-alert")
def reset_alert_route(request: Request, task_id: str) -> dict:
    user = require_user(request)
    return reset_alert_state(task_id, user)


@router.post("/tasks/{task_id}/check-now")
def check_now_route(request: Request, task_id: str) -> dict:
    user = require_user(request)
    result = run_monitor_check(user.id, task_id)
    return result


# ============================================================
# Process Filter Preview API
# ============================================================

class PreviewFilterPayload(BaseModel):
    serverId: str
    matchMode: str = "simple"
    matchPattern: str = ""
    filterUser: str = ""


@router.post("/preview-filter")
def preview_filter_route(request: Request, payload: PreviewFilterPayload) -> dict:
    user = require_user(request)
    result = preview_process_filter(
        server_id=payload.serverId,
        match_mode=payload.matchMode,
        match_pattern=payload.matchPattern,
        filter_user=payload.filterUser,
        user=user,
    )
    return result


# ============================================================
# Alert Action APIs
# ============================================================

class EmailActionConfig(BaseModel):
    emailRecipients: list[str] = Field(default_factory=list)
    emailSubjectTemplate: str = "实验监控报警: {task_name}"
    emailBodyTemplate: str = ""


class ScriptActionConfig(BaseModel):
    scriptCommands: list[str] = Field(default_factory=list)
    scriptScreenName: str = ""
    scriptsPerTrigger: int = 1


class CreateAlertActionPayload(BaseModel):
    actionType: str  # email | script
    emailRecipients: list[str] = Field(default_factory=list)
    emailSubjectTemplate: str = "实验监控报警: {task_name}"
    emailBodyTemplate: str = ""
    scriptCommands: list[str] = Field(default_factory=list)
    scriptScreenName: str = ""
    scriptsPerTrigger: int = 1


@router.get("/tasks/{task_id}/actions")
def list_actions_route(request: Request, task_id: str) -> dict:
    user = require_user(request)
    return {"actions": list_alert_actions(task_id, user)}


@router.post("/tasks/{task_id}/actions")
def create_action_route(request: Request, task_id: str, payload: CreateAlertActionPayload) -> dict:
    user = require_user(request)
    return {"action": create_alert_action(task_id, payload.model_dump(), user)}


@router.put("/actions/{action_id}")
def update_action_route(request: Request, action_id: str, payload: CreateAlertActionPayload) -> dict:
    user = require_user(request)
    return {"action": update_alert_action(action_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/actions/{action_id}")
def delete_action_route(request: Request, action_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_alert_action(action_id, user)
    return {"deleted": True}


# ============================================================
# Script Groups APIs
# ============================================================

class ScriptGroupPayload(BaseModel):
    name: str = "脚本组"
    screenNamePrefix: str = ""
    sortOrder: int = 0
    commands: list[str] = Field(default_factory=list)


class QueueItemPayload(BaseModel):
    command: str


class ReorderPayload(BaseModel):
    orderedIds: list[str]


@router.get("/actions/{action_id}/groups")
def list_groups_route(request: Request, action_id: str) -> dict:
    user = require_user(request)
    return {"groups": list_script_groups(action_id, user)}


@router.post("/actions/{action_id}/groups")
def create_group_route(request: Request, action_id: str, payload: ScriptGroupPayload) -> dict:
    user = require_user(request)
    return {"group": create_script_group(action_id, payload.model_dump(), user)}


@router.put("/groups/{group_id}")
def update_group_route(request: Request, group_id: str, payload: ScriptGroupPayload) -> dict:
    user = require_user(request)
    return {"group": update_script_group(group_id, payload.model_dump(exclude_unset=True), user)}


@router.delete("/groups/{group_id}")
def delete_group_route(request: Request, group_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_script_group(group_id, user)
    return {"deleted": True}


# ── Queue ──────────────────────────────────────────────────────────────────────

@router.post("/groups/{group_id}/queue")
def add_queue_item_route(request: Request, group_id: str, payload: QueueItemPayload) -> dict:
    user = require_user(request)
    return {"item": add_queue_item(group_id, payload.command, user)}


@router.delete("/queue/{item_id}")
def delete_queue_item_route(request: Request, item_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_queue_item(item_id, user)
    return {"deleted": True}


@router.post("/groups/{group_id}/queue/reorder")
def reorder_queue_route(request: Request, group_id: str, payload: ReorderPayload) -> dict:
    user = require_user(request)
    return {"group": reorder_queue(group_id, payload.orderedIds, user)}


# ── History ───────────────────────────────────────────────────────────────────

@router.post("/history/{history_id}/restore")
def restore_history_route(request: Request, history_id: str) -> dict:
    user = require_user(request)
    return {"group": restore_history_to_queue(history_id, user)}


@router.delete("/history/{history_id}")
def delete_history_route(request: Request, history_id: str) -> dict[str, bool]:
    user = require_user(request)
    delete_history_item(history_id, user)
    return {"deleted": True}


# ── Screen Sessions ───────────────────────────────────────────────────────────

@router.post("/groups/{group_id}/sessions/refresh")
def refresh_sessions_route(request: Request, group_id: str) -> dict:
    user = require_user(request)
    return {"sessions": refresh_screen_sessions(group_id, user)}
