import mimetypes
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.app.core.errors import ToolboxError
from backend.app.core.security import require_user, require_user_tool_data_dir
from tools.url_navigator.backend.service import (
    create_link as create_link_svc,
    delete_link as delete_link_svc,
    list_links as list_links_svc,
    reset_links as reset_links_svc,
    update_link as update_link_svc,
    update_link_icon as update_link_icon_svc,
    get_link,
)

router = APIRouter()

TOOL_ID = "url_navigator"
MAX_ICON_SIZE = 1024 * 1024
ICON_EXTENSIONS = {".ico", ".png", ".jpg", ".jpeg", ".webp", ".svg"}


class LinkIcon(BaseModel):
    source: str = "none"
    filename: str | None = None
    updatedAt: str | None = None

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value not in {"auto", "custom", "none"}:
            raise ValueError("图标来源必须是 auto、custom 或 none")
        return value


class LinkEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    label: str
    url: str
    probeUrl: str = ""
    priority: int = 10

    @field_validator("label", "url")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("probeUrl")
    @classmethod
    def clean_probe_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("url", "probeUrl")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value:
            return value
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return value


class LinkBase(BaseModel):
    name: str
    description: str = ""
    category: str = "未分类"
    strategy: str = "priority_first"
    entries: list[LinkEntry]
    icon: LinkIcon = Field(default_factory=LinkIcon)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名称不能为空")
        return value

    @field_validator("description", "category")
    @classmethod
    def clean_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("entries")
    @classmethod
    def require_entries(cls, value: list[LinkEntry]) -> list[LinkEntry]:
        if not value:
            raise ValueError("至少需要一个访问入口")
        return value

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, value: str) -> str:
        if value not in {"latency_first", "priority_first"}:
            raise ValueError("策略必须是 latency_first 或 priority_first")
        return value


class LinkCreate(LinkBase):
    pass


class LinkUpdate(LinkBase):
    pass


class Link(LinkBase):
    id: str = Field(default_factory=lambda: uuid4().hex)
    createdAt: str
    updatedAt: str


class FaviconParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        rel = values.get("rel", "").lower()
        if "icon" in rel and values.get("href"):
            self.hrefs.append(values.get("href", ""))


@router.get("/links", response_model=list[Link])
def list_links(request: Request) -> list[Link]:
    user = require_user(request)
    return [Link.model_validate(item) for item in list_links_svc(user.id)]


@router.post("/links", response_model=Link)
def create_link(request: Request, payload: dict) -> Link:
    user = require_user(request)
    link_payload = _validate_payload(LinkCreate, payload)
    created = create_link_svc(user.id, link_payload.model_dump())
    return Link.model_validate(created)


@router.put("/links/{link_id}", response_model=Link)
def update_link(request: Request, link_id: str, payload: dict) -> Link:
    user = require_user(request)
    link_payload = _validate_payload(LinkUpdate, payload)
    # Preserve existing icon (icon updates go through dedicated endpoints)
    existing = get_link(user.id, link_id)
    if existing is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    merged = link_payload.model_dump()
    merged["icon"] = existing.get("icon", {})
    updated = update_link_svc(user.id, link_id, merged)
    if updated is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    return Link.model_validate(updated)


@router.delete("/links/{link_id}")
def delete_link(request: Request, link_id: str) -> dict[str, bool]:
    user = require_user(request)
    existing = get_link(user.id, link_id)
    if existing is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    _delete_icon_file(data_dir, LinkIcon.model_validate(existing.get("icon", {})))
    delete_link_svc(user.id, link_id)
    return {"deleted": True}


@router.post("/links/reset", response_model=list[Link])
def reset_links(request: Request) -> list[Link]:
    user = require_user(request)
    links = reset_links_svc(user.id)
    return [Link.model_validate(item) for item in links]


@router.get("/links/{link_id}/icon")
def get_icon(request: Request, link_id: str) -> FileResponse:
    user = require_user(request)
    link = get_link(user.id, link_id)
    if link is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    icon = LinkIcon.model_validate(link.get("icon", {}))
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    path = _icon_path(data_dir, icon)
    if path is None or not path.exists():
        raise ToolboxError("ICON_NOT_FOUND", "图标不存在", status_code=404, tool_id=TOOL_ID)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.post("/links/{link_id}/icon/upload", response_model=Link)
async def upload_icon(request: Request, link_id: str, file: UploadFile = File(...)) -> Link:
    user = require_user(request)
    existing = get_link(user.id, link_id)
    if existing is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    original_name = Path(file.filename or "icon").name
    suffix = _safe_icon_suffix(original_name)
    content = await file.read(MAX_ICON_SIZE + 1)
    if len(content) > MAX_ICON_SIZE:
        raise ToolboxError("ICON_TOO_LARGE", "图标不能超过 1MB", status_code=400, tool_id=TOOL_ID)
    filename = f"{link_id}-custom{suffix}"
    _icons_dir(data_dir).mkdir(parents=True, exist_ok=True)
    (_icons_dir(data_dir) / filename).write_bytes(content)
    # Remove old icon file if any
    _delete_icon_file(data_dir, LinkIcon.model_validate(existing.get("icon", {})))
    updated = update_link_icon_svc(user.id, link_id, {
        "source": "custom",
        "filename": filename,
        "updatedAt": _now(),
    })
    return Link.model_validate(updated)


@router.post("/links/{link_id}/icon/refresh", response_model=Link)
def refresh_icon(request: Request, link_id: str) -> Link:
    user = require_user(request)
    existing = get_link(user.id, link_id)
    if existing is None:
        raise ToolboxError("LINK_NOT_FOUND", "导航项不存在", status_code=404, tool_id=TOOL_ID)
    link = Link.model_validate(existing)
    try:
        icon_url = _discover_icon_url(link.entries[0].url)
        content, suffix = _download_icon(icon_url)
    except (OSError, URLError, TimeoutError) as exc:
        raise ToolboxError("ICON_REFRESH_FAILED", "自动抓取图标失败", status_code=400, tool_id=TOOL_ID) from exc
    filename = f"{link_id}-auto-{uuid4().hex[:8]}{suffix}"
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    _icons_dir(data_dir).mkdir(parents=True, exist_ok=True)
    (_icons_dir(data_dir) / filename).write_bytes(content)
    # Remove old icon file if any
    _delete_icon_file(data_dir, LinkIcon.model_validate(existing.get("icon", {})))
    updated = update_link_icon_svc(user.id, link_id, {
        "source": "auto",
        "filename": filename,
        "updatedAt": _now(),
    })
    return Link.model_validate(updated)


def _validate_payload(model: type[LinkCreate] | type[LinkUpdate], payload: dict) -> LinkCreate | LinkUpdate:
    try:
        normalized = dict(payload)
        normalized.setdefault("icon", {})
        return model.model_validate(normalized)
    except ValidationError as exc:
        details = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        raise ToolboxError("INVALID_LINK", "导航数据不合法", status_code=400, tool_id=TOOL_ID, extra={"details": details}) from exc


def _discover_icon_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    try:
        html = _read_url(page_url, MAX_ICON_SIZE).decode("utf-8", errors="ignore")
    except (OSError, URLError, TimeoutError):
        return fallback
    parser = FaviconParser()
    parser.feed(html)
    if parser.hrefs:
        return urljoin(page_url, parser.hrefs[0])
    return fallback


def _download_icon(icon_url: str) -> tuple[bytes, str]:
    content = _read_url(icon_url, MAX_ICON_SIZE + 1)
    if len(content) > MAX_ICON_SIZE:
        raise ToolboxError("ICON_TOO_LARGE", "图标不能超过 1MB", status_code=400, tool_id=TOOL_ID)
    suffix = _suffix_from_url(icon_url) or ".ico"
    if suffix not in ICON_EXTENSIONS:
        suffix = ".ico"
    return content, suffix


def _read_url(url: str, limit: int) -> bytes:
    request = UrlRequest(url, headers={"User-Agent": "pansis-toolbox/1.0"})
    with urlopen(request, timeout=5) as response:
        return response.read(limit)


def _safe_icon_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ICON_EXTENSIONS:
        raise ToolboxError("INVALID_ICON_TYPE", "只支持 ico、png、jpg、jpeg、webp、svg 图标", status_code=400, tool_id=TOOL_ID)
    return suffix


def _suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]+", suffix) else ""


def _icons_dir(data_dir: Path) -> Path:
    return data_dir / "icons"


def _icon_path(data_dir: Path, icon: LinkIcon) -> Path | None:
    if not icon.filename:
        return None
    return _icons_dir(data_dir) / Path(icon.filename).name


def _delete_icon_file(data_dir: Path, icon: LinkIcon) -> None:
    path = _icon_path(data_dir, icon)
    if path and path.exists():
        path.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
