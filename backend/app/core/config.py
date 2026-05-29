from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "pansis_toolbox"
    app_env: str = "development"
    api_prefix: str = "/api"
    tools_dir: Path = Path("tools")
    storage_dir: Path = Path("storage")
    widget_layout_path: Path = Path("storage/data/widget_layout.json")
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
