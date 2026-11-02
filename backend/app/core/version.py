from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path


PROJECT_NAME = "pansis-toolbox"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"


def get_project_version() -> str:
    """Return the base application version declared in pyproject.toml.

    A packaged installation may not contain its original pyproject.toml, so
    installed distribution metadata is the only fallback.  Keeping the source
    checkout path first also avoids stale editable-install metadata in local
    development.
    """
    try:
        return _version_from_pyproject(PYPROJECT_FILE)
    except (OSError, ValueError):
        try:
            return distribution_version(PROJECT_NAME)
        except PackageNotFoundError as exc:
            raise RuntimeError("Unable to determine the project version") from exc


def get_build_version() -> str:
    """Return this build's version, including an optional commit suffix."""
    base_version = get_project_version()
    build_version = os.getenv("APP_BUILD_VERSION", "").strip()
    if build_version == base_version or build_version.startswith(f"{base_version}-"):
        return build_version
    return base_version


def _version_from_pyproject(path: Path) -> str:
    in_project_section = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project_section = line == "[project]"
            continue
        if not in_project_section or not line.startswith("version"):
            continue
        key, separator, raw_value = line.partition("=")
        if separator and key.strip() == "version":
            value = raw_value.strip().strip('"\'')
            if value:
                return value
    raise ValueError(f"Missing project.version in {path}")


if __name__ == "__main__":
    print(get_project_version())
