from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.app.core.security import require_user
from tools.clash_subscription_manager.backend import service

router = APIRouter()


class SourcePayload(BaseModel):
    name: str = ""
    url: str
    userAgent: str = ""
    refreshSeconds: int = Field(default=21600, ge=60, le=604800)
    enabled: bool = True


class SourcePatch(BaseModel):
    name: str | None = None
    url: str | None = None
    userAgent: str | None = None
    refreshSeconds: int | None = Field(default=None, ge=60, le=604800)
    enabled: bool | None = None


class ProfilePayload(BaseModel):
    name: str = "未命名聚合"
    targetKernel: Literal["mihomo", "clash"] = "mihomo"
    settings: dict[str, Any] = {}
    ruleSetId: str | None = None


class ProfilePatch(BaseModel):
    name: str | None = None
    targetKernel: Literal["mihomo", "clash"] | None = None
    settings: dict[str, Any] | None = None
    ruleSetId: str | None = None


class SelectionPayload(BaseModel):
    stableIdentities: list[str] = Field(default_factory=list, max_length=5000)


class RuleSetPayload(BaseModel):
    name: str = "规则库"
    rules: list[Any] = Field(default_factory=list)
    providers: dict[str, Any] = Field(default_factory=dict)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    importMeta: dict[str, Any] = Field(default_factory=dict)


class RuleImportPayload(BaseModel):
    name: str = "导入规则"
    content: str = ""
    url: str = ""


class ProbePayload(BaseModel):
    nodeIds: list[str] = Field(default_factory=list, max_length=1000)


@router.get("/dashboard")
def get_dashboard(request: Request) -> dict[str, Any]:
    return service.dashboard(require_user(request))


@router.get("/sources")
def get_sources(request: Request) -> dict[str, Any]:
    return {"sources": service.list_sources(require_user(request))}


@router.post("/sources")
def post_source(request: Request, payload: SourcePayload) -> dict[str, Any]:
    return {"source": service.create_source(payload.model_dump(), require_user(request))}


@router.put("/sources/{source_id}")
def put_source(request: Request, source_id: str, payload: SourcePatch) -> dict[str, Any]:
    return {"source": service.update_source(source_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/sources/{source_id}")
def remove_source(request: Request, source_id: str) -> dict[str, bool]:
    service.delete_source(source_id, require_user(request)); return {"deleted": True}


@router.post("/sources/{source_id}/refresh")
def refresh_source(request: Request, source_id: str) -> dict[str, Any]:
    return service.refresh_source(source_id, require_user(request))


@router.get("/nodes")
def get_nodes(request: Request, protocol: str = "", sourceId: str = "", search: str = "") -> dict[str, Any]:
    return {"nodes": service.list_nodes(require_user(request), {"protocol": protocol, "sourceId": sourceId, "search": search})}


@router.post("/nodes/probe")
def probe_nodes(request: Request, payload: ProbePayload) -> dict[str, Any]:
    return {"results": service.probe_nodes(payload.nodeIds, require_user(request)), "notice": "TCP 可达不代表代理可用或真实延迟。"}


@router.get("/profiles")
def get_profiles(request: Request) -> dict[str, Any]:
    return {"profiles": service.list_profiles(require_user(request))}


@router.post("/profiles")
def post_profile(request: Request, payload: ProfilePayload) -> dict[str, Any]:
    return {"profile": service.create_profile(payload.model_dump(), require_user(request))}


@router.put("/profiles/{profile_id}")
def put_profile(request: Request, profile_id: str, payload: ProfilePatch) -> dict[str, Any]:
    return {"profile": service.update_profile(profile_id, payload.model_dump(exclude_unset=True), require_user(request))}


@router.delete("/profiles/{profile_id}")
def remove_profile(request: Request, profile_id: str) -> dict[str, bool]:
    service.delete_profile(profile_id, require_user(request)); return {"deleted": True}


@router.put("/profiles/{profile_id}/selections")
def put_selections(request: Request, profile_id: str, payload: SelectionPayload) -> dict[str, Any]:
    return service.set_profile_selections(profile_id, payload.stableIdentities, require_user(request))


@router.post("/profiles/{profile_id}/validate")
def validate_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.validate_profile(profile_id, require_user(request))


@router.post("/profiles/{profile_id}/preview")
def preview_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.preview_profile(profile_id, require_user(request))


@router.post("/profiles/{profile_id}/publish")
def publish_profile(request: Request, profile_id: str) -> dict[str, Any]:
    return service.publish_profile(profile_id, require_user(request))


@router.post("/profiles/{profile_id}/rotate-token")
def rotate_token(request: Request, profile_id: str) -> dict[str, Any]:
    return service.rotate_profile_token(profile_id, require_user(request))


@router.get("/rule-sets")
def get_rule_sets(request: Request) -> dict[str, Any]:
    return {"ruleSets": service.list_rule_sets(require_user(request))}


@router.post("/rule-sets")
def post_rule_set(request: Request, payload: RuleSetPayload) -> dict[str, Any]:
    return {"ruleSet": service.save_rule_set(payload.model_dump(), require_user(request))}


@router.put("/rule-sets/{rule_set_id}")
def put_rule_set(request: Request, rule_set_id: str, payload: RuleSetPayload) -> dict[str, Any]:
    return {"ruleSet": service.save_rule_set(payload.model_dump(), require_user(request), rule_set_id)}


@router.delete("/rule-sets/{rule_set_id}")
def remove_rule_set(request: Request, rule_set_id: str) -> dict[str, bool]:
    service.delete_rule_set(rule_set_id, require_user(request)); return {"deleted": True}


@router.post("/rule-sets/import")
def import_rule_set(request: Request, payload: RuleImportPayload) -> dict[str, Any]:
    return {"ruleSet": service.import_rule_set(payload.model_dump(), require_user(request))}


@router.get("/refresh-runs")
def get_refresh_runs(request: Request, limit: int = 100) -> dict[str, Any]:
    return {"runs": service.refresh_runs(require_user(request), limit)}


def mount_extra(app: FastAPI) -> None:
    @app.get("/sub/clash/{token}", include_in_schema=False)
    def public_clash_subscription(token: str) -> Response:
        value = service.public_subscription(token)
        if value is None: return Response(status_code=404)
        content, digest, created_at, name = value
        # YAML accepts a quoted filename token; keep response headers safe.
        filename = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:80] or "clash-subscription"
        try:
            last_modified = format_datetime(datetime.fromisoformat(created_at).astimezone(timezone.utc), usegmt=True)
        except ValueError:
            last_modified = created_at
        return Response(content=content, media_type="application/yaml", headers={"Content-Disposition": f'attachment; filename="{filename}.yaml"', "ETag": f'"{digest}"', "Last-Modified": last_modified})
