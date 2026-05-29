from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


class ToolboxError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        tool_id: str | None = None,
        extra: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.tool_id = tool_id
        self.extra = extra or {}
        super().__init__(message)


async def toolbox_error_handler(request: Request, exc: ToolboxError) -> JSONResponse:
    error = {
        "code": exc.code,
        "message": exc.message,
        "toolId": exc.tool_id,
        "requestId": request.headers.get("x-request-id", f"req_{uuid4().hex[:12]}"),
    }
    error.update(exc.extra)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "服务发生未处理异常",
                "toolId": None,
                "requestId": request.headers.get("x-request-id", f"req_{uuid4().hex[:12]}"),
            }
        },
    )
