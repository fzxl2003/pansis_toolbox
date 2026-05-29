from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.registry.loader import discover_tools
from backend.app.registry.models import ToolStatus


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tools_list_contains_text_cleaner() -> None:
    response = client.get("/api/tools")
    assert response.status_code == 200
    tools = response.json()
    text_cleaner = next(tool for tool in tools if tool["id"] == "text_cleaner")
    assert text_cleaner["status"] == "available"
    assert text_cleaner["api"]["prefix"] == "/api/tools/text-cleaner"


def test_text_cleaner_api() -> None:
    response = client.post(
        "/api/tools/text-cleaner/clean",
        json={"text": "  Hello     Toolbox  \n\n", "trim": True, "collapseWhitespace": True, "removeBlankLines": True},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Hello Toolbox"


def test_widgets_api() -> None:
    response = client.get("/api/widgets")
    assert response.status_code == 200
    widgets = response.json()
    assert widgets[0]["id"] == "text_cleaner.summary"

    data_response = client.get("/api/widgets/text_cleaner.summary/data")
    assert data_response.status_code == 200
    assert data_response.json()["widgetId"] == "text_cleaner.summary"


def test_discover_tools_marks_missing_entries() -> None:
    tools = discover_tools(Path("tools"))
    assert any(tool.tool_id == "text_cleaner" and tool.status == ToolStatus.available for tool in tools)
