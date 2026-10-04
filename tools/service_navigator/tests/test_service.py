from __future__ import annotations

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User
from tools.service_navigator.backend import access, database, health, navigation, scanning, service


@pytest.fixture
def owner() -> User:
    return User(id="service-owner", username="owner", display_name="Owner")


@pytest.fixture
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(database, "root_dir", lambda: tmp_path)
    database.RECOVERY_DONE = False
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


def test_fixed_target_pages_support_ordering(isolated_storage, owner: User) -> None:
    service.create_site({"title": "Lab", "slug": "lab-services"}, owner)
    first = service.add_target({"label": "First", "address": "10.0.0.1"}, owner)
    second = service.add_target({"label": "Second", "address": "10.0.0.2"}, owner)
    third = service.add_target({"label": "Third", "address": "10.0.0.3"}, owner)
    assert [target["id"] for target in service.list_targets(owner)] == [first["id"], second["id"], third["id"]]

    service.reorder_targets([third["id"], first["id"], second["id"]], owner)
    site = service._owner_site(owner)
    assert site is not None
    assert [target["id"] for target in service.list_targets(owner)] == [third["id"], first["id"], second["id"]]
    assert [page["targetId"] for page in service.public_navigation(site)["targetPages"]] == [third["id"], first["id"], second["id"]]

    with pytest.raises(ToolboxError) as incomplete:
        service.reorder_targets([third["id"], first["id"]], owner)
    assert incomplete.value.code == "INVALID_TARGET_ORDER"
    with pytest.raises(ToolboxError) as duplicate:
        service.reorder_targets([third["id"], first["id"], first["id"]], owner)
    assert duplicate.value.code == "INVALID_TARGET_ORDER"


def test_site_appearance_persists_only_valid_theme_accent_and_card_opacity(isolated_storage, owner: User) -> None:
    service.create_site({"title": "Lab", "slug": "lab-services"}, owner)
    updated = service.update_site({"theme": "light", "accentColor": " #7C3AED ", "cardOpacity": 62}, owner)
    assert updated["site"]["theme"] == "light"
    assert updated["site"]["accentColor"] == "#7c3aed"
    assert updated["site"]["cardOpacity"] == 62
    reset = service.update_site({"theme": "sepia", "accentColor": "purple", "cardOpacity": 999}, owner)
    assert reset["site"]["theme"] == "auto"
    assert reset["site"]["accentColor"] == "#4f7cff"
    assert reset["site"]["cardOpacity"] == 100


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
    assert ssh["commandDescription"] == "ssh -p 22 <user>@10.0.0.8"
    assert ssh["serviceTemplate"] == "generic"
    service.update_service(ssh["id"], {"displayName": "Gateway", "connectionCommand": "ssh lab"}, owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "OpenSSH", "version": "9.1", "extraInfo": "", "tunnel": "", "resolvedAddresses": ["10.0.0.9"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    updated = service.get_site(owner)["services"][0]
    assert updated["displayName"] == "Gateway"
    assert updated["commandDescription"] == "ssh lab"
    assert updated["resolvedAddresses"] == ["10.0.0.9"]
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [])
    assert service.get_site(owner)["services"][0]["state"] == "offline"


def test_port_service_templates_overwrite_details_and_reject_unknown(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 3389, "protocol": "tcp", "serviceName": "ms-wbt-server", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": [], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    item = service.get_site(owner)["services"][0]
    updated = service.update_service(item["id"], {"serviceType": "port", "serviceTemplate": "rdp", "description": "ignored", "commandDescription": "ignored"}, owner)
    assert updated["serviceTemplate"] == "rdp"
    assert updated["description"] == "通过远程桌面客户端连接此主机。"
    assert updated["commandDescription"] == "xfreerdp /v:10.0.0.8:3389 /u:<user>"
    with pytest.raises(ToolboxError) as error:
        service.update_service(item["id"], {"serviceTemplate": "telnet"}, owner)
    assert error.value.code == "INVALID_SERVICE_TEMPLATE"


def test_legacy_connection_command_migrates_to_command_description(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": [], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""}])
    service_id = service.get_site(owner)["services"][0]["id"]
    with service.conn() as database_connection:
        database_connection.execute("UPDATE service_navigator_services SET command_description='',connection_command='legacy ssh command' WHERE id=?", (service_id,))
        database_connection.commit()
    database.SERVICE_TEMPLATE_COMPAT_DATABASES.discard(str(database.db_path()))
    migrated = service.get_site(owner)["services"][0]
    assert migrated["commandDescription"] == "legacy ssh command"


def test_python_scan_aggregates_addresses_and_custom_ports(monkeypatch, isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    monkeypatch.setattr(scanning, "resolve_addresses", lambda _address: ["10.0.0.8", "10.0.0.9"])
    received: list[tuple[str, tuple[int, ...]]] = []

    def fake_scan(address: str, ports: tuple[int, ...]) -> list[dict]:
        received.append((address, ports))
        return [{"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "", "version": "", "extraInfo": "", "tunnel": ""}]

    monkeypatch.setattr(scanning, "_scan_address", fake_scan)
    monkeypatch.setattr(scanning, "_enrich_web_service", lambda _address, _item: {"httpTitle": "", "detectedUrl": "", "faviconFilename": ""})
    result = service._scan_target({"id": target["id"], "address": target["address"], "custom_ports": target["customPorts"]})

    assert result["ok"] is True
    assert result["openPorts"] == [22]
    assert [address for address, _ports in received] == ["10.0.0.8", "10.0.0.9"]
    assert all(22 in ports and 8080 in ports and len(ports) == 1001 for _address, ports in received)
    assert service.get_site(owner)["services"][0]["resolvedAddresses"] == ["10.0.0.8", "10.0.0.9"]


def test_scan_run_persists_target_progress(isolated_storage, owner: User) -> None:
    _site, target = _site_and_target(owner)
    service.update_target(target["id"], {"address": target["address"], "customPorts": "1-20000"}, owner)
    queued = service.request_scan(owner)
    assert service.get_scan(queued["id"], owner)["summary"] == {"targetCount": 1, "completedTargetCount": 0, "successCount": 0, "portCount": 20000, "completedPortCount": 0}
    with service.conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET status='running' WHERE id=?", (queued["id"],))
        database.commit()
    service._update_run_progress(queued["id"], completed=0, total=1, successes=0, completed_ports=250, total_ports=20000)
    assert service.get_scan(queued["id"], owner)["summary"] == {"targetCount": 1, "completedTargetCount": 0, "successCount": 0, "portCount": 20000, "completedPortCount": 250}


def test_private_access_password_is_revocable(monkeypatch, isolated_storage, owner: User) -> None:
    reader = User(id="service-reader", username="reader", display_name="Reader")
    monkeypatch.setattr(access, "list_users", lambda: [owner, reader])
    _site, _target = _site_and_target(owner)
    monkeypatch.setattr(access, "can_access_tool", lambda _tool_id, _user: True)
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
    monkeypatch.setattr(health, "platform_send_email", lambda recipients, subject, body: sent.append((recipients, subject, body)))
    monkeypatch.setattr(health, "_check_http_health", lambda _url: {"status": "unhealthy", "statusCode": 503, "latencyMs": 9, "finalUrl": "http://health/", "error": "HTTP 503"})
    for _ in range(2):
        service._collect_site_health(site, manual=False)
    assert sent == []
    service._collect_site_health(site, manual=False)
    assert len(sent) == 1
    assert "HTTP 503" in sent[0][2]
    service._collect_site_health(site, manual=False)
    assert len(sent) == 2  # cooldown is zero
    monkeypatch.setattr(health, "_check_http_health", lambda _url: {"status": "healthy", "statusCode": 302, "latencyMs": 5, "finalUrl": "http://health/login", "error": ""})
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
    monkeypatch.setattr(health, "platform_send_email", lambda *_args: pytest.fail("manual check must not send email"))
    monkeypatch.setattr(health, "_check_http_health", lambda _url: {"status": "unhealthy", "statusCode": 500, "latencyMs": 1, "finalUrl": "http://health/", "error": "HTTP 500"})
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
    assert service.NAV_SIZES["wide"] == (4, 2)
    service._persist_target_scan({"id": target["id"], "address": target["address"]}, [
        {"port": 22, "protocol": "tcp", "serviceName": "ssh", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "", "detectedUrl": "", "faviconFilename": ""},
        {"port": 8080, "protocol": "tcp", "serviceName": "http", "product": "", "version": "", "extraInfo": "", "resolvedAddresses": ["10.0.0.8"], "httpTitle": "Console", "detectedUrl": "http://10.0.0.8:8080", "faviconFilename": ""},
    ])
    services = service.get_site(owner)["services"]
    http = next(item for item in services if item["serviceType"] == "http")
    port = next(item for item in services if item["serviceType"] == "port")
    page = service.create_nav_page({"name": "常用"}, owner)
    icon = service.create_nav_icon({"name": "控制台", "size": "large", "serviceIds": [http["id"]], "iconSource": "text", "iconText": "控"}, owner)
    item = service.create_nav_item({"pageId": page["id"], "iconId": icon["id"], "size": "large"}, owner)
    assert set(item["layouts"]) == {"16"}
    assert item["size"] == "large"
    with pytest.raises(ToolboxError) as error:
        service.create_nav_icon({"name": "错误混用", "serviceIds": [http["id"], port["id"]]}, owner)
    assert error.value.code == "MIXED_NAV_SERVICES"
    second_page = service.create_nav_page({"name": "第二页"}, owner)
    second_item = service.create_nav_item({"pageId": second_page["id"], "iconId": icon["id"], "size": "small"}, owner)
    updated = service.update_nav_icon(icon["id"], {"name": "统一控制台", "iconText": "服"}, owner)
    assert updated["name"] == "统一控制台"
    assert all(page["items"][0]["name"] == "统一控制台" for page in [service.get_navigation(owner)["pages"][0], service.get_navigation(owner)["pages"][1]])
    service.delete_nav_item(second_item["id"], owner)
    assert any(entry["id"] == icon["id"] for entry in service.list_nav_icons(owner))
    layout = service.save_nav_layout(page["id"], 16, [{"itemId": item["id"], "x": 0, "y": 2}], owner)
    stored = layout["pages"][0]["items"][0]["layouts"]
    assert stored["16"] == {"x": 0, "y": 2}
    with pytest.raises(ToolboxError):
        service.save_nav_layout(page["id"], 4, [{"itemId": item["id"], "x": 1, "y": 0}], owner)
    canvas = service.save_nav_canvas(page["id"], [{"itemId": item["id"], "size": "wide", "x": 4, "y": 3}], owner)
    assert canvas["pages"][0]["items"][0]["size"] == "wide"
    assert canvas["pages"][0]["items"][0]["layouts"]["16"] == {"x": 4, "y": 3}
    second_icon = service.create_nav_icon({"name": "终端", "serviceIds": [port["id"]], "iconSource": "text", "iconText": "终"}, owner)
    second_item = service.create_nav_item({"pageId": page["id"], "iconId": second_icon["id"], "size": "small"}, owner)
    with pytest.raises(ToolboxError):
        service.save_nav_canvas(page["id"], [
            {"itemId": item["id"], "size": "wide", "x": 4, "y": 3},
            {"itemId": second_item["id"], "size": "small", "x": 4, "y": 3},
        ], owner)
    with pytest.raises(ToolboxError):
        service.save_nav_canvas(page["id"], [
            {"itemId": item["id"], "size": "large", "x": 14, "y": 0},
            {"itemId": second_item["id"], "size": "small", "x": 0, "y": 0},
        ], owner)
    duplicate = service.create_nav_item({"pageId": page["id"], "iconId": second_icon["id"], "size": "medium"}, owner)
    assert duplicate["iconId"] == second_icon["id"]
    assert len(service.get_navigation(owner)["pages"][0]["items"]) == 3
    public = service.public_navigation(_site)
    assert set(public["pages"][0]["items"][0]["layouts"]) == {"16", "12", "8", "4"}
    assert "icons" not in public
    owner_navigation = service.public_navigation(_site, include_icons=True)
    detected_icons = [entry for entry in owner_navigation["icons"] if entry["detectedServiceId"]]
    assert {entry["detectedServiceId"] for entry in detected_icons} == {http["id"], port["id"]}


def test_external_navigation_icon_fetches_favicon_and_cleans_replaced_files(isolated_storage, owner: User, monkeypatch) -> None:
    site, _target = _site_and_target(owner)
    page = service.create_nav_page({"name": "常用"}, owner)
    downloaded: list[str] = []

    def fake_download(url: str) -> str:
        filename = f"external-{len(downloaded)}.ico"
        (database.navigation_icon_dir() / filename).write_bytes(url.encode())
        downloaded.append(filename)
        return filename

    monkeypatch.setattr(navigation, "_download_external_favicon", fake_download)
    icon = service.create_nav_icon({
        "name": "文档", "destinationType": "external", "externalUrl": "https://docs.example",
        "iconSource": "favicon", "iconText": "文",
    }, owner)
    assert icon["destinationType"] == "external"
    assert icon["externalUrl"] == "https://docs.example"
    assert icon["serviceIds"] == []
    assert icon["iconUrl"].endswith(icon["id"])
    first_file = downloaded[-1]
    assert (database.navigation_icon_dir() / first_file).is_file()

    item = service.create_nav_item({"pageId": page["id"], "iconId": icon["id"], "size": "medium"}, owner)
    public_item = service.public_navigation(site)["pages"][0]["items"][0]
    assert public_item["id"] == item["id"]
    assert public_item["destinationType"] == "external"
    assert public_item["externalUrl"] == "https://docs.example"
    assert public_item["services"] == []

    updated = service.update_nav_icon(icon["id"], {"externalUrl": "https://guide.example", "destinationType": "external", "iconSource": "favicon"}, owner)
    second_file = downloaded[-1]
    assert updated["externalUrl"] == "https://guide.example"
    assert not (database.navigation_icon_dir() / first_file).exists()
    assert (database.navigation_icon_dir() / second_file).is_file()

    service.update_nav_icon(icon["id"], {"destinationType": "external", "iconSource": "text", "iconText": "链"}, owner)
    assert not (database.navigation_icon_dir() / second_file).exists()
    service.update_nav_icon(icon["id"], {"destinationType": "external", "iconSource": "favicon"}, owner)
    third_file = downloaded[-1]
    assert (database.navigation_icon_dir() / third_file).is_file()
    service.delete_nav_icon(icon["id"], owner)
    assert not (database.navigation_icon_dir() / third_file).exists()
    with pytest.raises(ToolboxError) as error:
        service.create_nav_icon({"name": "缺少链接", "destinationType": "external", "externalUrl": ""}, owner)
    assert error.value.code == "INVALID_NAVIGATION_URL"
