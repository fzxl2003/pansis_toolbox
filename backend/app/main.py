from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import routes_auth, routes_health, routes_settings, routes_tools, routes_widgets
from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError, toolbox_error_handler, unhandled_error_handler
from backend.app.core.logging import configure_logging
from backend.app.registry.loader import discover_tools, register_tool_routers
from backend.app.services.auth_service import ensure_default_user
from backend.app.services.scheduler_service import scheduler


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

    @app.on_event("startup")
    async def start_scheduler() -> None:
        await scheduler.start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        await scheduler.stop()

    return app


app = create_app()
