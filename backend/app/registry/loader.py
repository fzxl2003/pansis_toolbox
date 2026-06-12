from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend.app.registry.models import RegisteredTool, ToolManifest, ToolStatus
from backend.app.registry.tool_registry import tool_registry
from backend.app.registry.widget_registry import widget_registry
from backend.app.services.scheduler_service import Scheduler, scheduler

logger = logging.getLogger(__name__)


def discover_tools(tools_dir: Path) -> list[RegisteredTool]:
    tools: list[RegisteredTool] = []
    seen_ids: set[str] = set()
    seen_prefixes: set[str] = set()

    if not tools_dir.exists():
        logger.info("Tools directory does not exist: %s", tools_dir)
        return []

    for tool_root in sorted(path for path in tools_dir.iterdir() if path.is_dir()):
        manifest_path = tool_root / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = ToolManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
            status = _initial_status(tool_root, manifest, seen_ids, seen_prefixes)
            error = _status_error(status)
            tools.append(RegisteredTool(manifest=manifest, root_path=tool_root, status=status, error_message=error))
            seen_ids.add(manifest.id)
            seen_prefixes.add(manifest.api.prefix)
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            fallback = ToolManifest(
                id=tool_root.name,
                name=tool_root.name,
                description="Manifest 加载失败",
                version="0.0.0",
                enabled=False,
                entry={"frontend": "frontend/index.tsx", "backend": "backend/router.py"},
                api={"prefix": f"/api/tools/{tool_root.name}"},
            )
            tools.append(
                RegisteredTool(
                    manifest=fallback,
                    root_path=tool_root,
                    status=ToolStatus.failed,
                    error_message=str(exc),
                )
            )

    _apply_dependency_status(tools)
    return tools


def register_tool_routers(app: FastAPI, tools: list[RegisteredTool]) -> None:
    for tool in tools:
        if tool.status != ToolStatus.available:
            continue
        assets_dir = tool.root_path / "assets"
        if assets_dir.exists():
            app.mount(f"/tool-assets/{tool.tool_id}", StaticFiles(directory=assets_dir), name=f"tool-assets:{tool.tool_id}")
        try:
            router = load_backend_router(tool)
            app.include_router(router, prefix=tool.api_prefix, tags=[f"tool:{tool.tool_id}"])
            register_tool_scheduled_tasks(tool, scheduler)
        except Exception as exc:  # noqa: BLE001 - isolate bad tools from the host app.
            logger.exception("Failed to load tool router for %s", tool.tool_id)
            tool.status = ToolStatus.failed
            tool.error_message = str(exc)

    tool_registry.replace_all(tools)
    widget_registry.rebuild_from_tools(tools)


def load_backend_router(tool: RegisteredTool) -> APIRouter:
    router_path = tool.root_path / tool.manifest.entry.backend
    module_name = f"toolbox_tools.{tool.tool_id}.router"
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import router from {router_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    router = getattr(module, "router", None)
    if not isinstance(router, APIRouter):
        raise RuntimeError("Tool backend entry must expose a FastAPI APIRouter named 'router'")
    return router


def register_tool_scheduled_tasks(tool: RegisteredTool, target_scheduler: Scheduler) -> None:
    scheduler_path = tool.root_path / "backend" / "scheduler.py"
    if not scheduler_path.exists():
        return
    module_name = f"toolbox_tools.{tool.tool_id}.scheduler"
    spec = importlib.util.spec_from_file_location(module_name, scheduler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import scheduler from {scheduler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register_tasks = getattr(module, "register_tasks", None)
    if not callable(register_tasks):
        raise RuntimeError("Tool scheduler entry must expose a callable register_tasks(scheduler)")
    register_tasks(target_scheduler)


def _initial_status(
    tool_root: Path,
    manifest: ToolManifest,
    seen_ids: set[str],
    seen_prefixes: set[str],
) -> ToolStatus:
    if manifest.id in seen_ids or manifest.api.prefix in seen_prefixes:
        return ToolStatus.failed
    if not manifest.enabled:
        return ToolStatus.disabled
    if not (tool_root / manifest.entry.frontend).exists():
        return ToolStatus.missing_frontend
    if not (tool_root / manifest.entry.backend).exists():
        return ToolStatus.missing_backend
    return ToolStatus.available


def _apply_dependency_status(tools: list[RegisteredTool]) -> None:
    by_id = {tool.tool_id: tool for tool in tools}
    for tool in tools:
        if tool.status != ToolStatus.available:
            continue
        missing = [dep for dep in tool.manifest.dependsOn if dep not in by_id or by_id[dep].status != ToolStatus.available]
        if missing:
            tool.status = ToolStatus.dependency_failed
            tool.error_message = f"Unavailable dependencies: {', '.join(missing)}"


def _status_error(status: ToolStatus) -> str | None:
    if status == ToolStatus.failed:
        return "Duplicate tool id or API prefix"
    if status == ToolStatus.missing_frontend:
        return "Frontend entry is missing"
    if status == ToolStatus.missing_backend:
        return "Backend entry is missing"
    if status == ToolStatus.disabled:
        return "Tool is disabled"
    return None
