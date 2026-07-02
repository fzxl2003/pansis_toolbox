from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "pansis_toolbox"
    app_env: str = "development"
    api_prefix: str = "/api"
    tools_dir: Path = Path("tools")
    frontend_dist_dir: Path = Path("frontend/dist")
    storage_dir: Path = Path("storage")
    platform_db_path: Path = Path("storage/data/platform.db")
    widget_layout_path: Path = Path("storage/data/widget_layout.json")
    frontend_origin: str = "http://localhost:5173"
    session_cookie_name: str = "pansis_session"
    session_secret: str = "change-me-in-production"
    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"
    default_admin_display_name: str = "本地管理员"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
