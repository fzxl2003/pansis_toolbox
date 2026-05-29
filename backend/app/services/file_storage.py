from pathlib import Path
from uuid import uuid4

from backend.app.core.config import get_settings


def allocate_temp_path(filename: str) -> Path:
    temp_dir = get_settings().storage_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir / f"{uuid4().hex}_{Path(filename).name}"
