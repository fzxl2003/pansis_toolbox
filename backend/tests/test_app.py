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
    assert any(tool.tool_id == "memo_demo" and tool.status == ToolStatus.available for tool in tools)


def test_auth_me_anonymous() -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


def test_login_and_logout() -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True

    me_response = client.get("/api/auth/me", cookies=response.cookies)
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == "admin"

    logout_response = client.post("/api/auth/logout", cookies=response.cookies)
    assert logout_response.status_code == 200
    assert logout_response.json()["authenticated"] is False


def test_login_wrong_password() -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_sso_is_reserved() -> None:
    response = client.get("/api/auth/sso/login")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_memo_demo_requires_login() -> None:
    response = client.get("/api/tools/memo-demo/memos")
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "LOGIN_REQUIRED"
    assert payload["error"]["loginUrl"] == "/login"


def test_memo_demo_upload_list_view_delete() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert auth.status_code == 200
    cookies = auth.cookies

    upload = client.post(
        "/api/tools/memo-demo/upload",
        files={"file": ("note.txt", b"First memo\nhello", "text/plain")},
        cookies=cookies,
    )
    assert upload.status_code == 200
    memo = upload.json()
    assert memo["title"] == "First memo"

    listing = client.get("/api/tools/memo-demo/memos", cookies=cookies)
    assert listing.status_code == 200
    assert any(item["id"] == memo["id"] for item in listing.json())

    detail = client.get(f"/api/tools/memo-demo/memos/{memo['id']}", cookies=cookies)
    assert detail.status_code == 200
    assert detail.json()["content"] == "First memo\nhello"

    delete = client.delete(f"/api/tools/memo-demo/memos/{memo['id']}", cookies=cookies)
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
