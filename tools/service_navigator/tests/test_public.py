from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.errors import ToolboxError, toolbox_error_handler
from backend.app.services.auth_service import User
from tools.service_navigator.backend import access, database, public, service


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ToolboxError, toolbox_error_handler)
    public.mount_extra(app)
    return app


def test_private_public_page_hides_fingerprint_and_exposes_all_services(monkeypatch, tmp_path) -> None:
    owner = User(id="public-owner", username="owner", display_name="Owner")
    monkeypatch.setattr(database, "root_dir", lambda: tmp_path)
    database.RECOVERY_DONE = False
    monkeypatch.setattr(access, "list_users", lambda: [owner])
    monkeypatch.setattr(access, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda _request: None)
    service.create_site({"title": "Lab", "slug": "lab-public"}, owner)
    target = service.add_target({"label": "Gateway", "address": "10.0.0.8"}, owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "OpenSSH", "version": "9.5", "extraInfo": "Ubuntu", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    password = service.add_password("Guests", "secret", owner)
    client = TestClient(_app())
    gate = client.get("/service-nav/lab-public")
    assert gate.status_code == 401
    assert "访问密码" in gate.text
    assert client.post("/service-nav/lab-public/unlock", json={"password": "secret"}).status_code == 200
    page = client.get("/service-nav/lab-public")
    assert page.status_code == 200
    assert "OpenSSH" not in page.text
    assert '"serviceType": "port"' in page.text
    assert '"commandDescription": "ssh -p 22 <user>@10.0.0.8"' in page.text
    assert "全部服务" not in page.text
    assert 'data-page="target:' in page.text
    assert "SERVICE NAVIGATOR" not in page.text
    assert "data-search-engine" not in page.text
    assert "data-engine=\"google\"" in page.text
    assert "data-toggle-sidebar" in page.text
    service.update_password(password["id"], {"label": "Guests", "password": "new-secret", "enabled": True}, owner)
    assert client.get("/service-nav/lab-public").status_code == 401
