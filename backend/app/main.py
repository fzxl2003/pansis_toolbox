from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api import routes_auth, routes_health, routes_settings, routes_tools, routes_widgets
from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError, toolbox_error_handler, unhandled_error_handler
from backend.app.core.logging import configure_logging
from backend.app.registry.loader import discover_tools, register_tool_routers
from backend.app.services.auth_service import ensure_default_user
from backend.app.services.scheduler_service import scheduler
from backend.app.services.ssh_connection_service import close_all as close_ssh_connections


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ToolboxError, toolbox_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(routes_health.router, prefix=settings.api_prefix)
    app.include_router(routes_auth.router, prefix=settings.api_prefix)
    app.include_router(routes_tools.router, prefix=settings.api_prefix)
    app.include_router(routes_widgets.router, prefix=settings.api_prefix)
    app.include_router(routes_settings.router, prefix=settings.api_prefix)

    ensure_default_user()
    tools = discover_tools(settings.tools_dir)
    register_tool_routers(app, tools)
    mount_frontend(app, settings.frontend_dist_dir)

    @app.on_event("startup")
    async def start_scheduler() -> None:
        await scheduler.start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        await scheduler.stop()
        close_ssh_connections()

    return app


def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    index_path = dist_dir / "index.html"
    if not index_path.exists():
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def serve_frontend_index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "tool-assets/")):
            raise HTTPException(status_code=404)

        requested_path = dist_dir / full_path
        if requested_path.is_file():
            return FileResponse(requested_path)
        return FileResponse(index_path)


app = create_app()
