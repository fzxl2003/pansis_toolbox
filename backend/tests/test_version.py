from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.version import _version_from_pyproject, get_build_version, get_project_version
from backend.app.main import app
from backend.app.registry.loader import discover_tools


def test_build_version_accepts_only_base_or_commit_suffix(monkeypatch) -> None:
    base_version = get_project_version()
    monkeypatch.setenv("APP_BUILD_VERSION", f"{base_version}-08c969c")
    assert get_build_version() == f"{base_version}-08c969c"

    monkeypatch.setenv("APP_BUILD_VERSION", "9.9.9-unrelated")
    assert get_build_version() == base_version


def test_all_tool_versions_are_injected() -> None:
    tools = discover_tools(Path("tools"))
    assert tools
    assert {tool.manifest.version for tool in tools} == {get_project_version()}


def test_about_uses_project_version() -> None:
    response = TestClient(app).get("/api/settings/about")
    assert response.status_code == 200
    version_item = next(item for item in response.json()["items"] if item["label"] == "版本")
    assert version_item["value"] == get_project_version()


def test_pyproject_parser_reads_only_project_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nversion = "wrong"\n\n[project]\nname = "demo"\nversion = "2.4.6"\n',
        encoding="utf-8",
    )
    assert _version_from_pyproject(pyproject) == "2.4.6"
