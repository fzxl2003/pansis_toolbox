from __future__ import annotations

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User
from tools.service_navigator.backend import service


@pytest.fixture
def owner() -> User:
    return User(id="service-owner", username="owner", display_name="Owner")


@pytest.fixture
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(service, "root_dir", lambda: tmp_path)
    service.RECOVERY_DONE = False
    return tmp_path


def _site_and_target(owner: User) -> tuple[dict, dict]:
    service.create_site({"title": "Lab", "slug": "lab-services"}, owner)
    target = service.add_target({"label": "Lab host", "address": "10.0.0.8", "customPorts": "22,8080"}, owner)
    site = service._owner_site(owner)
    assert site is not None
    return site, target


def test_target_validation_and_limits(isolated_storage, owner: User) -> None:
    service.create_site({"title": "Lab", "slug": "lab-services"}, owner)
    assert service.normalize_ports("8080,22,8000-8002") == "22,8000-8002,8080"
    with pytest.raises(ToolboxError):
        service.add_target({"address": "https://example.com"}, owner)
    assert service.normalize_ports("1-300") == "1-300"
    assert 30000 in service.ports_for_target("1-30000")
    with pytest.raises(ToolboxError):
        service.normalize_ports("65536")
    for index in range(service.MAX_TARGETS):
        service.add_target({"address": f"10.0.0.{index + 1}"}, owner)
    with pytest.raises(ToolboxError) as error:
        service.add_target({"address": "10.0.1.1"}, owner)
    assert error.value.code == "TARGET_LIMIT"


def test_site_appearance_persists_only_valid_theme_and_accent(isolated_storage, owner: User) -> None:
    service.create_site({"title": "Lab", "slug": "lab-services"}, owner)
    updated = service.update_site({"theme": "light", "accentColor": " #7C3AED "}, owner)
    assert updated["site"]["theme"] == "light"
    assert updated["site"]["accentColor"] == "#7c3aed"
    reset = service.update_site({"theme": "sepia", "accentColor": "purple"}, owner)
    assert reset["site"]["theme"] == "auto"
    assert reset["site"]["accentColor"] == "#4f7cff"


def test_python_fingerprints_and_rescan_preserves_overrides(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    ssh = service._apply_banner_fingerprint(
        {"port": 22, "protocol": "tcp", "serviceName": "unknown", "product": "", "version": "", "extraInfo": "", "tunnel": ""},
        b"SSH-2.0-OpenSSH_9.0p1 Ubuntu-3\r\n",
    )
    assert ssh["serviceName"] == "ssh"
    assert ssh["product"] == "OpenSSH"
    assert ssh["version"] == "9.0p1"
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{**ssh, "resolvedAddresses": ["10.0.0.8"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    detail = service.get_site(owner)
    assert detail is not None
    ssh = detail["services"][0]
    assert ssh["connectionCommand"] == "ssh -p 22 <user>@10.0.0.8"
    service.update_service(ssh["id"], {"displayName": "Gateway", "connectionCommand": "ssh lab"}, owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "OpenSSH", "version": "9.1", "extraInfo": "", "tunnel": "", "resolvedAddresses": ["10.0.0.9"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    updated = service.get_site(owner)["services"][0]
    assert updated["displayName"] == "Gateway"
    assert updated["connectionCommand"] == "ssh lab"
    assert updated["resolvedAddresses"] == ["10.0.0.9"]
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [])
    assert service.get_site(owner)["services"][0]["state"] == "offline"


def test_python_scan_aggregates_addresses_and_custom_ports(monkeypatch, isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    monkeypatch.setattr(service, "resolve_addresses", lambda _address: ["10.0.0.8", "10.0.0.9"])
    received: list[tuple[str, tuple[int, ...]]] = []

    def fake_scan(address: str, ports: tuple[int, ...]) -> list[dict]:
        received.append((address, ports))
        return [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "", "version": "", "extraInfo": "", "tunnel": ""}]

    monkeypatch.setattr(service, "_scan_address", fake_scan)
    monkeypatch.setattr(service, "_enrich_web_service", lambda _address, _item: {"httpTitle": "", "detectedUrl": "", "faviconFilename": ""})
    result = service._scan_target({"id": target["id"], "address": target["address"], "custom_ports": target["customPorts"]})

    assert result["ok"] is True
    assert result["openPorts"] == [22]
    assert [address for address, _ports in received] == ["10.0.0.8", "10.0.0.9"]
    assert all(22 in ports and 8080 in ports and len(ports) == 1001 for _address, ports in received)
    assert service.get_site(owner)["services"][0]["resolvedAddresses"] == ["10.0.0.8", "10.0.0.9"]


def test_scan_run_persists_target_progress(isolated_storage, owner: User) -> None:
    _site, _target = _site_and_target(owner)
    queued = service.request_scan(owner)
    assert service.get_scan(queued["id"], owner)["summary"] == {"targetCount": 1, "completedTargetCount": 0, "successCount": 0}
    with service.conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET status='running' WHERE id=?", (queued["id"],))
        database.commit()
    service._update_run_progress(queued["id"], completed=1, total=1, successes=1)
    assert service.get_scan(queued["id"], owner)["summary"] == {"targetCount": 1, "completedTargetCount": 1, "successCount": 1}


def test_private_access_password_is_revocable(monkeypatch, isolated_storage, owner: User) -> None:
    reader = User(id="service-reader", username="reader", display_name="Reader")
    monkeypatch.setattr(service, "list_users", lambda: [owner, reader])
    _site, _target = _site_and_target(owner)
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    private = service.public_site("lab-services")
    assert private is not None
    assert service.site_access(private, None)["allowed"] is False
    service.add_access_user("reader", owner)
    assert service.site_access(private, reader)["allowed"] is True
    password = service.add_password("Guests", "secret", owner)
    service.unlock_site(private, "secret", "browser-one")
    assert service.site_access(private, None, "browser-one")["allowed"] is True
    service.update_password(password["id"], {"label": "Guests", "password": "new-secret", "enabled": True}, owner)
    assert service.site_access(private, None, "browser-one")["allowed"] is False


def test_http_health_alert_confirmation_repeat_and_recovery(monkeypatch, isolated_storage, owner: User) -> None:
    site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 8080, "protocol": "tcp", "serviceName": "http", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "Console", "detectedUrl": "http://10.0.0.8:8080/", "faviconFilename": ""}])
    web = next(item for item in service.get_site(owner)["services"] if item["port"] == 8080)
    settings = service.update_health_settings({"checkIntervalSeconds": 60, "emailRecipients": ["ops@example.com"], "confirmCount": 3, "repeatIntervalSeconds": 0, "maxRepeatCount": 0}, owner)
    assert settings["confirmCount"] == 3
    sent: list[tuple[list[str], str, str]] = []
    monkeypatch.setattr(service, "platform_send_email", lambda recipients, subject, body: sent.append((recipients, subject, body)))
    monkeypatch.setattr(service, "_check_http_health", lambda _url: {"status": "unhealthy", "statusCode": 503, "latencyMs": 9, "finalUrl": "http://health/", "error": "HTTP 503"})
    for _ in range(2):
        service._collect_site_health(site, manual=False)
    assert sent == []
    service._collect_site_health(site, manual=False)
    assert len(sent) == 1
    assert "HTTP 503" in sent[0][2]
    service._collect_site_health(site, manual=False)
    assert len(sent) == 2  # cooldown is zero
    monkeypatch.setattr(service, "_check_http_health", lambda _url: {"status": "healthy", "statusCode": 302, "latencyMs": 5, "finalUrl": "http://health/login", "error": ""})
    service._collect_site_health(site, manual=False)
    assert len(sent) == 3
    assert "已恢复" in sent[-1][1]
    updated = next(item for item in service.get_site(owner)["services"] if item["id"] == web["id"])
    assert updated["healthStatus"] == "healthy"
    assert len(service.list_health_snapshots(web["id"], owner)) == 5


def test_manual_health_check_never_sends_alert(monkeypatch, isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 8080, "protocol": "tcp", "serviceName": "http", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "Console", "detectedUrl": "http://10.0.0.8:8080/", "faviconFilename": ""}])
    web = next(item for item in service.get_site(owner)["services"] if item["port"] == 8080)
    service.update_health_settings({"emailRecipients": ["ops@example.com"], "confirmCount": 1}, owner)
    monkeypatch.setattr(service, "platform_send_email", lambda *_args: pytest.fail("manual check must not send email"))
    monkeypatch.setattr(service, "_check_http_health", lambda _url: {"status": "unhealthy", "statusCode": 500, "latencyMs": 1, "finalUrl": "http://health/", "error": "HTTP 500"})
    result = service.check_health(web["id"], owner)
    assert result["status"] == "unhealthy"
    assert service.list_health_events(owner) == []


def test_public_health_output_is_only_a_coarse_status(isolated_storage, owner: User) -> None:
    site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 8080, "protocol": "tcp", "serviceName": "http", "product": "nginx", "version": "1.25", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "Console", "detectedUrl": "http://10.0.0.8:8080/", "faviconFilename": ""}])
    web = next(item for item in service.get_site(owner)["services"] if item["port"] == 8080)
    with service.conn() as database:
        database.execute("UPDATE service_navigator_services SET health_status='unhealthy',last_health_status_code=503,last_health_latency_ms=77,last_health_error='private failure' WHERE id=?", (web["id"],))
        database.commit()
    public = service.public_services(site)[0]
    assert public["healthStatus"] == "unhealthy"
    assert public["healthMonitored"] is True
    assert "lastHealthStatusCode" not in public
    assert "lastHealthLatencyMs" not in public
    assert "lastHealthError" not in public
    assert "healthUrl" not in public


def test_service_type_can_override_scanner_and_changes_form_fields(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 8080, "protocol": "tcp", "serviceName": "unknown", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    item = service.get_site(owner)["services"][0]
    assert item["serviceType"] == "http"  # common web port is the initial suggestion
    updated = service.update_service(item["id"], {"serviceType": "port", "connectionCommand": "telnet host 8080", "healthEnabled": True, "navigationUrl": "http://wrong.example"}, owner)
    assert updated["serviceType"] == "port"
    assert updated["healthEnabled"] is False
    assert updated["navigationUrl"] == ""
    updated = service.update_service(item["id"], {"serviceType": "http", "healthEnabled": True, "healthUrl": "http://host/health", "navigationUrl": "http://host"}, owner)
    assert updated["serviceType"] == "http"
    assert updated["healthEnabled"] is True
    assert updated["healthUrl"] == "http://host/health"


def test_navigation_uses_one_editable_layout_and_derives_narrow_grids(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [
        {"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""},
        {"port": 8080, "protocol": "tcp", "serviceName": "http", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "Console", "detectedUrl": "http://10.0.0.8:8080", "faviconFilename": ""},
    ])
    services = service.get_site(owner)["services"]
    http = next(item for item in services if item["serviceType"] == "http")
    port = next(item for item in services if item["serviceType"] == "port")
    page = service.create_nav_page({"name": "常用"}, owner)
    item = service.create_nav_item({"pageId": page["id"], "name": "控制台", "size": "large", "serviceIds": [http["id"]], "iconSource": "none"}, owner)
    assert set(item["layouts"]) == {"16"}
    assert item["size"] == "large"
    with pytest.raises(ToolboxError) as error:
        service.create_nav_item({"pageId": page["id"], "name": "错误混用", "size": "small", "serviceIds": [http["id"], port["id"]]}, owner)
    assert error.value.code == "MIXED_NAV_SERVICES"
    layout = service.save_nav_layout(page["id"], 16, [{"itemId": item["id"], "x": 0, "y": 2}], owner)
    stored = layout["pages"][0]["items"][0]["layouts"]
    assert stored["16"] == {"x": 0, "y": 2}
    with pytest.raises(ToolboxError):
        service.save_nav_layout(page["id"], 4, [{"itemId": item["id"], "x": 1, "y": 0}], owner)
    public = service.public_navigation(_site)
    assert set(public["pages"][0]["items"][0]["layouts"]) == {"16", "12", "8", "4"}
