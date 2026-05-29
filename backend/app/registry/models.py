from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    available = "available"
    disabled = "disabled"
    failed = "failed"
    missing_frontend = "missing_frontend"
    missing_backend = "missing_backend"
    dependency_failed = "dependency_failed"


class ToolEntry(BaseModel):
    frontend: str
    backend: str


class ToolApi(BaseModel):
    prefix: str


class WidgetSize(BaseModel):
    w: int = 2
    h: int = 1


class WidgetManifest(BaseModel):
    id: str
    name: str
    type: str = "summary"
    backend: str | None = None
    defaultSize: WidgetSize = Field(default_factory=WidgetSize)


class ToolPermissions(BaseModel):
    filesystem: bool = False
    network: bool = False
    longRunningTask: bool = False


class ToolManifest(BaseModel):
    id: str
    name: str
    description: str
    version: str
    enabled: bool = True
    category: str = "other"
    icon: str = "wrench"
    entry: ToolEntry
    api: ToolApi
    widgets: list[WidgetManifest] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)
    permissions: ToolPermissions = Field(default_factory=ToolPermissions)
    dependsOn: list[str] = Field(default_factory=list)


class RegisteredTool(BaseModel):
    manifest: ToolManifest
    root_path: Path
    status: ToolStatus
    error_message: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def tool_id(self) -> str:
        return self.manifest.id

    @property
    def api_prefix(self) -> str:
        return self.manifest.api.prefix

    def public_dict(self) -> dict[str, Any]:
        data = self.manifest.model_dump()
        data["status"] = self.status.value
        data["errorMessage"] = self.error_message
        return data


class RegisteredWidget(BaseModel):
    widget_id: str
    tool_id: str
    manifest: WidgetManifest
    tool_status: ToolStatus
    backend_path: Path | None = None

    model_config = {"arbitrary_types_allowed": True}

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.widget_id,
            "toolId": self.tool_id,
            "name": self.manifest.name,
            "type": self.manifest.type,
            "defaultSize": self.manifest.defaultSize.model_dump(),
            "toolStatus": self.tool_status.value,
        }
