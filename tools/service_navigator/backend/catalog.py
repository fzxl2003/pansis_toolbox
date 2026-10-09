"""Service catalog presentation and owner edits.."""

from __future__ import annotations

from .database import *
from .sites import _owner_site


def list_services(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.display_name,s.port,s.id", (site["id"],)).fetchall()
    return [_service_public(row) for row in rows]

def update_service(service_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("""SELECT s.*,t.address AS target_address FROM service_navigator_services s
            JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?""", (service_id, site["id"])).fetchone()
        if row is None:
            raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
        service_type = str(payload.get("serviceType", row["service_type"]) or "port").strip().lower()
        if service_type not in {"http", "port"}:
            raise ToolboxError("INVALID_SERVICE_TYPE", "服务类型必须是 HTTP 服务或端口服务", status_code=400, tool_id=TOOL_ID)
        if service_type != row["service_type"]:
            conflicting = database.execute("""SELECT 1 FROM service_navigator_nav_icon_services links
                JOIN service_navigator_nav_icon_services peers ON peers.icon_id=links.icon_id AND peers.service_id<>links.service_id
                JOIN service_navigator_services peer_service ON peer_service.id=peers.service_id
                WHERE links.service_id=? AND peer_service.service_type<>? LIMIT 1""", (service_id, service_type)).fetchone()
            if conflicting:
                raise ToolboxError("NAV_SERVICE_TYPE_CONFLICT", "该服务已与其他类型服务关联到同一图标；请先调整导航图标", status_code=409, tool_id=TOOL_ID)
        template = str(payload.get("serviceTemplate", row["service_template"]) or "generic").strip().lower()
        if template not in PORT_SERVICE_TEMPLATES:
            raise ToolboxError("INVALID_SERVICE_TEMPLATE", "服务模板不合法", status_code=400, tool_id=TOOL_ID)
        if service_type == "http":
            template = "generic"
        template_changed = service_type == "port" and template != row["service_template"]
        command_value = payload.get("commandDescription", payload.get("connectionCommand", row["command_description"] or row["connection_command"]))
        values = {
            "display_name": str(payload.get("displayName", row["display_name"]) or "").strip()[:120],
            "description": str(payload.get("description", row["description"]) or "").strip()[:500],
            "navigation_url": _clean_navigation_url(str(payload.get("navigationUrl", row["navigation_url"]) or "")),
            "command_description": str(command_value or "").strip()[:500],
            "service_template": template,
            "service_type": service_type,
            "health_enabled": 1 if payload.get("healthEnabled", bool(row["health_enabled"])) else 0,
            "health_url": _clean_health_url(str(payload.get("healthUrl", row["health_url"]) or "")),
            "updated_at": now_iso(),
            "id": service_id,
        }
        if template_changed:
            preset = PORT_SERVICE_TEMPLATES[template]
            values["description"] = preset["description"]
            values["command_description"] = preset["command"].format(host=row["target_address"], port=row["port"])
        if service_type == "port":
            values["navigation_url"] = ""
            values["health_enabled"] = 0
            values["health_url"] = ""
        if service_type != row["service_type"] or values["service_template"] != row["service_template"] or values["navigation_url"] != row["navigation_url"] or values["command_description"] != (row["command_description"] or row["connection_command"]):
            database.execute("""UPDATE service_navigator_nav_icons SET preference_revision=preference_revision+1,updated_at=?
                WHERE id IN (SELECT icon_id FROM service_navigator_nav_icon_services WHERE service_id=?)""", (values["updated_at"], service_id))
        database.execute("""UPDATE service_navigator_services SET display_name=:display_name,description=:description,navigation_url=:navigation_url,connection_command=:command_description,command_description=:command_description,service_template=:service_template,service_type=:service_type,health_enabled=:health_enabled,health_url=:health_url,updated_at=:updated_at WHERE id=:id""", values)
        database.commit()
        updated = database.execute("SELECT * FROM service_navigator_services WHERE id=?", (service_id,)).fetchone()
    return _service_public(updated)

def _clean_navigation_url(value: str) -> str:
    value = value.strip()[:1000]
    if value and not value.startswith(("http://", "https://")):
        raise ToolboxError("INVALID_NAVIGATION_URL", "导航 URL 必须以 http:// 或 https:// 开头", status_code=400, tool_id=TOOL_ID)
    return value

def _clean_health_url(value: str) -> str:
    """Health URLs are intentionally limited to direct HTTP(S) requests."""
    return _clean_navigation_url(value)

def default_command(service_name: str, host: str, port: int) -> str:
    normalized = service_name.lower()
    if normalized in {"postgres", "postgresql"}:
        normalized = "postgresql"
    elif normalized == "redis-server":
        normalized = "redis"
    template = COMMAND_TEMPLATES.get(normalized)
    return template.format(host=host, port=port) if template else f"{host}:{port}"

__all__ = ['_clean_health_url', '_clean_navigation_url', 'default_command', 'list_services', 'update_service']
