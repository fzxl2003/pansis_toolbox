from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import require_user
from tools.memo_demo.backend.service import (
    create_memo,
    delete_memo,
    get_memo,
    list_memos,
)

router = APIRouter()

MAX_TXT_SIZE = 512 * 1024


class MemoSummary(BaseModel):
    id: str
    title: str
    filename: str
    createdAt: str
    updatedAt: str
    sizeBytes: int


class MemoDetail(MemoSummary):
    content: str


@router.post("/upload", response_model=MemoSummary)
async def upload_memo(request: Request, file: UploadFile = File(...)) -> MemoSummary:
    user = require_user(request)
    original_name = file.filename or "memo.txt"
    if not original_name.lower().endswith(".txt"):
        raise ToolboxError("INVALID_FILE_TYPE", "只支持上传 .txt 文件", status_code=400, tool_id="memo_demo")

    content_bytes = await file.read()
    if not content_bytes:
        raise ToolboxError("EMPTY_FILE", "文件内容为空", status_code=400, tool_id="memo_demo")
    if len(content_bytes) > MAX_TXT_SIZE:
        raise ToolboxError("FILE_TOO_LARGE", "文件不能超过 512KB", status_code=400, tool_id="memo_demo")

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolboxError("INVALID_TEXT_ENCODING", "请上传 UTF-8 编码的 TXT 文件", status_code=400, tool_id="memo_demo") from exc

    title = _title_from_content(content, original_name)
    memo = create_memo(user.id, title, content)
    return MemoSummary(**memo)


@router.get("/memos", response_model=list[MemoSummary])
def list_memos_route(request: Request) -> list[MemoSummary]:
    user = require_user(request)
    return [MemoSummary(**item) for item in list_memos(user.id)]


@router.get("/memos/{memo_id}", response_model=MemoDetail)
def get_memo_route(request: Request, memo_id: str) -> MemoDetail:
    user = require_user(request)
    memo = get_memo(user.id, memo_id)
    if memo is None:
        raise ToolboxError("MEMO_NOT_FOUND", "备忘录不存在", status_code=404, tool_id="memo_demo")
    return MemoDetail(**memo)


@router.delete("/memos/{memo_id}")
def delete_memo_route(request: Request, memo_id: str) -> dict[str, bool]:
    user = require_user(request)
    if not delete_memo(user.id, memo_id):
        raise ToolboxError("MEMO_NOT_FOUND", "备忘录不存在", status_code=404, tool_id="memo_demo")
    return {"deleted": True}


def _title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:40]
    return fallback[:40] or "未命名备忘录"
