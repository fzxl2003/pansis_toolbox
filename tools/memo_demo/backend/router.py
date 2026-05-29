from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel

from backend.app.core.errors import ToolboxError
from backend.app.core.security import require_user_tool_data_dir

router = APIRouter()

MAX_TXT_SIZE = 512 * 1024
INDEX_FILE = "memos.json"


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
    data_dir = require_user_tool_data_dir(request, "memo_demo")
    original_name = Path(file.filename or "memo.txt").name
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

    memo_id = uuid4().hex
    stored_filename = f"{memo_id}.txt"
    now = datetime.now(timezone.utc).isoformat()
    title = _title_from_content(content, original_name)
    summary = MemoSummary(
        id=memo_id,
        title=title,
        filename=stored_filename,
        createdAt=now,
        updatedAt=now,
        sizeBytes=len(content_bytes),
    )

    (data_dir / stored_filename).write_text(content, encoding="utf-8")
    memos = _load_index(data_dir)
    memos.insert(0, summary.model_dump())
    _save_index(data_dir, memos)
    return summary


@router.get("/memos", response_model=list[MemoSummary])
def list_memos(request: Request) -> list[MemoSummary]:
    data_dir = require_user_tool_data_dir(request, "memo_demo")
    return [MemoSummary.model_validate(item) for item in _load_index(data_dir)]


@router.get("/memos/{memo_id}", response_model=MemoDetail)
def get_memo(request: Request, memo_id: str) -> MemoDetail:
    data_dir = require_user_tool_data_dir(request, "memo_demo")
    item = _find_memo(data_dir, memo_id)
    content_path = data_dir / item["filename"]
    if not content_path.exists():
        raise ToolboxError("MEMO_CONTENT_MISSING", "备忘录内容文件不存在", status_code=404, tool_id="memo_demo")
    return MemoDetail(**item, content=content_path.read_text(encoding="utf-8"))


@router.delete("/memos/{memo_id}")
def delete_memo(request: Request, memo_id: str) -> dict[str, bool]:
    data_dir = require_user_tool_data_dir(request, "memo_demo")
    memos = _load_index(data_dir)
    target = next((item for item in memos if item["id"] == memo_id), None)
    if target is None:
        raise ToolboxError("MEMO_NOT_FOUND", "备忘录不存在", status_code=404, tool_id="memo_demo")
    content_path = data_dir / target["filename"]
    if content_path.exists():
        content_path.unlink()
    _save_index(data_dir, [item for item in memos if item["id"] != memo_id])
    return {"deleted": True}


def _load_index(data_dir: Path) -> list[dict]:
    index_path = data_dir / INDEX_FILE
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def _save_index(data_dir: Path, memos: list[dict]) -> None:
    (data_dir / INDEX_FILE).write_text(json.dumps(memos, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_memo(data_dir: Path, memo_id: str) -> dict:
    for item in _load_index(data_dir):
        if item["id"] == memo_id:
            return item
    raise ToolboxError("MEMO_NOT_FOUND", "备忘录不存在", status_code=404, tool_id="memo_demo")


def _title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:40]
    return fallback[:40] or "未命名备忘录"
