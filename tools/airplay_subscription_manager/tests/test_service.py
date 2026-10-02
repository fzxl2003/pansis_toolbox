from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from backend.app.core.config import Settings
from backend.app.core.errors import ToolboxError
from backend.app.db import database
from backend.app.services.auth_service import User
from tools.airplay_subscription_manager.backend import service


def test_legacy_tool_storage_is_moved_to_airplay_directory(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    previous = settings.storage_dir / "user_data" / "migration-user" / "tools" / service._legacy_tool_id()
    previous.mkdir(parents=True)
    (previous / "marker.txt").write_text("preserved", encoding="utf-8")

    service._migrate_legacy_tool_storage("migration-user")

    current = settings.storage_dir / "user_data" / "migration-user" / "tools" / service.TOOL_ID
    assert not previous.exists()
    assert (current / "marker.txt").read_text(encoding="utf-8") == "preserved"


def test_parse_yaml_base64_and_uri_variants() -> None:
    config = """proxies:
  - name: yaml-node
    type: socks5
    server: 127.0.0.1
    port: 1080
"""
    nodes, unsupported = service.parse_subscription(config)
    assert unsupported == 0
    assert nodes[0]["protocol"] == "socks5"
    ss = "ss://" + base64.urlsafe_b64encode(b"aes-128-gcm:password@example.org:443").decode().rstrip("=")
    encoded = base64.urlsafe_b64encode((ss + "#sample").encode()).decode().rstrip("=")
    nodes, unsupported = service.parse_subscription(encoded)
    assert unsupported == 0
    assert nodes[0]["protocol"] == "ss"
    assert nodes[0]["server"] == "example.org"
    vmess = {"v": "2", "ps": "vmess", "add": "v.example", "port": "443", "id": "00000000-0000-0000-0000-000000000001", "aid": "0", "net": "ws", "tls": "tls"}
    parsed = service.parse_uri("vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode())
    assert parsed and parsed["protocol"] == "vmess" and parsed["server"] == "v.example"


def test_profile_publish_and_token_lookup(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="user-1", username="one", display_name="One")
    service.init_database(user.id)
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    node = service.parse_uri("vless://00000000-0000-0000-0000-000000000001@example.org:443?security=reality&sni=example.org&fp=chrome&pbk=public-key&sid=abcd&flow=xtls-rprx-vision#vless")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
        node_id = conn.execute("SELECT id FROM csm_nodes WHERE name=?", (node["name"],)).fetchone()["id"]
    service.update_node_alias(node_id, "香港", user)
    provider = service.save_rule_provider({
        "name": "示例域名",
        "providerKey": "example-domains",
        "config": {"type": "manual", "behavior": "domain", "payload": ["example.com"]},
    }, user)
    rule_set = service.save_rule_set({
        "name": "basic rules",
        "groups": [{"name": "PROXY", "type": "select", "proxies": [node["name"]]}],
        "rules": ["MATCH,PROXY"],
        "providers": {},
        "importMeta": {"providerBindings": {"PROXY": [provider["id"]]}},
    }, user)
    profile = service.create_profile({"name": "my sub", "ruleSetId": rule_set["id"]}, user)
    assert profile["ruleSetName"] == "basic rules"
    assert "targetKernel" not in profile
    published = service.publish_profile(profile["id"], user)
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        node_row = conn.execute("SELECT id FROM csm_nodes WHERE name=?", (node["name"],)).fetchone()
        now = service._now()
        conn.execute("INSERT INTO csm_probe_results(id,node_id,reachable,latency_ms,error,created_at) VALUES(?,?,1,123,?,?)",
                     (service._id(), node_row["id"], "", now))
        conn.execute("INSERT INTO csm_refresh_runs(id,source_id,status,started_at,finished_at,duration_ms,nodes_before,nodes_after,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (service._id(), source["id"], "success", now, now, 120, 1, 1, "", now))
    assert service.list_profiles(user)[0]["ruleSetName"] == "basic rules"
    result = service.public_subscription(
        published["subscriptionToken"], client_ip="127.0.0.1", user_agent="pytest"
    )
    assert result is not None
    assert "vless" in result[0]
    assert "reality-opts" in result[0]
    details = service.public_subscription_details(published["subscriptionToken"])
    assert details is not None
    assert details["ruleSetName"] == "basic rules"
    assert details["proxies"][0]["name"] == "香港-vless"
    assert details["proxies"][0]["latencyMs"] == 123
    assert details["groups"][0]["providers"][0]["payload"] == ["example.com"]
    assert details["requestRuns"][0]["clientIp"] == "127.0.0.1"
    assert details["requestRuns"][0]["userAgent"] == "pytest"
    assert "vless" in details["yaml"]


def test_public_details_returns_scheduled_http_rule_provider_snapshot(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="public-provider-user", username="public-provider", display_name="Public Provider")
    service.init_database(user.id)
    remote_payload = [f"service-{index}.example" for index in range(8)]
    monkeypatch.setattr(service, "_download_rule_provider_payload", lambda url: remote_payload)

    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    node = service.parse_uri("vless://00000000-0000-0000-0000-000000000002@example.org:443?security=reality&sni=example.org&fp=chrome&pbk=public-key&sid=abcd&flow=xtls-rprx-vision#http-provider-node")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
    provider = service.save_rule_provider({
        "name": "HTTP 规则",
        "providerKey": "http-rules",
        "config": {"type": "http", "behavior": "domain", "url": "https://example.invalid/rules.yaml", "interval": 86400},
    }, user)
    assert provider["config"]["payload"] == remote_payload
    assert provider["config"]["fetchedAt"]
    assert provider["config"]["fetchError"] == ""

    rule_set = service.save_rule_set({
        "name": "http provider rules",
        "groups": [{"name": "PROXY", "type": "select", "proxies": [node["name"]]}],
        "rules": ["MATCH,PROXY"],
        "providers": {},
        "importMeta": {"providerBindings": {"PROXY": [provider["id"]]}},
    }, user)
    profile = service.create_profile({"name": "public provider sub", "ruleSetId": rule_set["id"]}, user)
    published = service.publish_profile(profile["id"], user)

    # Public visualization is served entirely from the persisted snapshot.
    def fail_download(url: str) -> list[str]:
        raise AssertionError("public details must not fetch URL Rule Providers")

    monkeypatch.setattr(service, "_download_rule_provider_payload", fail_download)
    details = service.public_subscription_details(published["subscriptionToken"])
    assert details is not None
    provider_details = details["groups"][0]["providers"][0]
    assert provider_details["kind"] == "规则订阅"
    assert provider_details["ruleCount"] == len(remote_payload)
    assert provider_details["payload"] == remote_payload
    assert provider_details["error"] == ""

    # The scheduler refreshes due snapshots outside the public request path.
    def expire_snapshot() -> None:
        with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
            conn.execute(
                "UPDATE csm_rule_providers SET config_json=json_set(config_json, '$.fetchedAt', ?) WHERE id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=86460)).isoformat(), provider["id"]),
            )

    def read_provider() -> dict[str, object]:
        return next(item for item in service.list_rule_providers(user) if item["id"] == provider["id"])

    expire_snapshot()
    monkeypatch.setattr(
        service,
        "_download_rule_provider_payload",
        lambda url: (_ for _ in ()).throw(ToolboxError("RULE_PROVIDER_FETCH_FAILED", "拉取失败", status_code=422)),
    )
    service.refresh_due_rule_providers()
    failed_provider = read_provider()
    assert failed_provider["config"]["payload"] == remote_payload
    assert failed_provider["config"]["fetchError"] == "拉取失败"

    expire_snapshot()
    refreshed_payload = [f"refreshed-{index}.example" for index in range(8)]
    monkeypatch.setattr(service, "_download_rule_provider_payload", lambda url: refreshed_payload)
    service.refresh_due_rule_providers()
    updated_provider = read_provider()
    assert updated_provider["config"]["payload"] == refreshed_payload
    assert updated_provider["config"]["fetchError"] == ""
    assert updated_provider["config"]["fetchedAt"] > provider["config"]["fetchedAt"]


def test_rule_set_and_public_domain_matching_use_local_snapshots(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="domain-test-user", username="domain-test", display_name="Domain Test")
    service.init_database(user.id)
    provider = service.save_rule_provider({
        "name": "AI 域名",
        "providerKey": "ai-domains",
        "config": {"type": "manual", "behavior": "domain", "payload": ["+.openai.example"]},
    }, user)
    rule_set = service.save_rule_set({
        "name": "domain rules",
        "groups": [{"name": "AI", "type": "select", "proxies": ["DIRECT"]}],
        "rules": ["MATCH,DIRECT"],
        "providers": {},
        "importMeta": {"providerBindings": {"AI": [provider["id"]]}},
    }, user)

    hit = service.test_rule_set_domain(rule_set["id"], "chat.openai.example", user)
    assert hit["matched"] is True
    assert hit["target"] == "AI"
    assert hit["rule"] == "DOMAIN-SUFFIX,openai.example,AI"
    fallback = service.test_rule_set_domain(rule_set["id"], "example.org", user)
    assert fallback["matched"] is True
    assert fallback["target"] == "DIRECT"
    assert fallback["rule"] == "MATCH,DIRECT"

    profile = service.create_profile({"name": "domain sub", "ruleSetId": rule_set["id"]}, user)
    published = service.publish_profile(profile["id"], user)

    def fail_download(url: str) -> list[str]:
        raise AssertionError("domain matching must use local snapshots")

    monkeypatch.setattr(service, "_download_rule_provider_payload", fail_download)
    public_hit = service.test_public_subscription_domain(published["subscriptionToken"], "chat.openai.example")
    assert public_hit is not None
    assert public_hit["matched"] is True
    assert public_hit["target"] == "AI"
    assert public_hit["rule"] == "DOMAIN-SUFFIX,openai.example,AI"

    with pytest.raises(ToolboxError) as invalid:
        service.test_rule_set_domain(rule_set["id"], "https://example.com/path", user)
    assert invalid.value.code == "INVALID_TEST_DOMAIN"


def test_airplay_output_rejects_unknown_node(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="user-2", username="two", display_name="Two")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    nodes, unsupported = service.parse_subscription("""proxies:
  - name: unsupported
    type: naive
    server: example.org
    port: 443
""")
    assert unsupported == 1
    node = nodes[0]
    assert not node["supported_output"]
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
    rule_set = service.save_rule_set({
        "name": "unsupported rules",
        "groups": [{"name": "PROXY", "type": "select", "proxies": [node["name"]]}],
        "rules": ["MATCH,PROXY"],
        "providers": {},
    }, user)
    profile = service.create_profile({"name": "compatible", "ruleSetId": rule_set["id"]}, user)
    result = service.validate_profile(profile["id"], user)
    assert not result["valid"]
    assert any(message["code"] == "INCOMPATIBLE_OUTPUT" for message in result["messages"])

    preview = service.preview_profile(profile["id"], user)
    assert not preview["valid"]
    assert "proxies: []" in preview["yaml"]
    assert any("不可输出 1 个" in line for line in preview["buildLog"])
    assert any(line.endswith("ERROR INCOMPATIBLE_OUTPUT：节点 unsupported（naive）不受 AirPlay 支持。") for line in preview["buildLog"])
    with pytest.raises(ToolboxError) as publish_error:
        service.publish_profile(profile["id"], user)
    assert publish_error.value.code == "PUBLISH_VALIDATION_FAILED"


def test_download_retries_transient_dns_failure(monkeypatch) -> None:
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise service.httpx.ConnectError("temporary DNS failure", request=request)
        return service.httpx.Response(200, content=b"proxies: []", request=request)

    real_client = service.httpx.Client
    transport = service.httpx.MockTransport(handler)
    monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    assert service._download("https://subscription.example/sub?token=secret") == b"proxies: []"
    assert attempts == 3



def test_profile_output_is_rule_driven_and_keeps_only_current_snapshot(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="rule-driven-user", username="rule-driven", display_name="Rule Driven")
    service.init_database(user.id)
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    nodes, unsupported = service.parse_subscription("""proxies:
  - name: node-a
    type: socks5
    server: 127.0.0.1
    port: 1080
  - name: node-b
    type: socks5
    server: 127.0.0.1
    port: 1081
""")
    assert unsupported == 0 and len(nodes) == 2
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], nodes)
    rule_set = service.save_rule_set({
        "name": "single node",
        "groups": [{"name": "PROXY", "type": "select", "proxies": ["node-a"]}],
        "rules": ["MATCH,PROXY"],
        "providers": {},
    }, user)
    profile = service.create_profile({
        "name": "rule driven",
        "ruleSetId": rule_set["id"],
        "settings": {"mode": "global"},
    }, user)
    published = service.publish_profile(profile["id"], user)
    document = yaml.safe_load(service.public_subscription(published["subscriptionToken"])[0])
    assert document["mode"] == "rule"
    assert [item["name"] for item in document["proxies"]] == ["node-a"]
    details = service.public_subscription_details(published["subscriptionToken"])
    assert details is not None
    assert details["mode"] == "rule"
    assert [item["name"] for item in details["proxies"]] == ["node-a"]
    service.publish_profile(profile["id"], user)
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        count = conn.execute("SELECT COUNT(*) FROM csm_published_snapshots WHERE profile_id=?", (profile["id"],)).fetchone()[0]
    assert count == 1

def test_download_error_redacts_subscription_query(monkeypatch) -> None:
    secret_url = "https://subscription.example/sub?token=must-not-leak"

    def handler(request):
        return service.httpx.Response(403, request=request)

    real_client = service.httpx.Client
    transport = service.httpx.MockTransport(handler)
    monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))

    try:
        service._download(secret_url)
    except Exception as exc:
        message = getattr(exc, "message", str(exc))
    else:
        raise AssertionError("expected download failure")

    assert "must-not-leak" not in message
    assert "HTTP 403" in message


def test_node_alias_and_original_name_resolve_in_rule_material(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="alias-user", username="alias", display_name="Alias")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    node = service.parse_uri("trojan://password@example.org:443?sni=example.org#source-name")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
        node_id = conn.execute("SELECT id FROM csm_nodes").fetchone()["id"]

    updated = service.update_node_alias(node_id, "friendly-name", user)
    assert updated["name"] == "source-name"
    assert updated["alias"] == "friendly-name"
    assert updated["displayName"] == "friendly-name"

    # A subscription refresh updates source material but must not erase the
    # user-owned alias.
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
        assert conn.execute("SELECT alias FROM csm_nodes WHERE id=?", (node_id,)).fetchone()["alias"] == "friendly-name"

    rule_set = service.save_rule_set({
        "name": "alias rules",
        "groups": [{"name": "PROXY", "type": "select", "proxies": ["source-name", "friendly-name"]}],
        "rules": ["DOMAIN,example.com,source-name", "IP-CIDR,10.0.0.0/8,source-name,no-resolve", "MATCH,friendly-name"],
        "providers": {},
    }, user)
    assert "groupName" not in rule_set
    profile = service.create_profile({"name": "alias profile", "ruleSetId": rule_set["id"]}, user)

    validation = service.validate_profile(profile["id"], user)
    assert validation["valid"]
    preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert preview["proxies"][0]["name"] == "friendly-name-source-name"
    assert preview["proxy-groups"][0]["proxies"] == ["friendly-name-source-name", "friendly-name-source-name"]
    assert preview["rules"] == [
        "DOMAIN,example.com,friendly-name-source-name",
        "IP-CIDR,10.0.0.0/8,friendly-name-source-name,no-resolve",
        "MATCH,friendly-name-source-name",
    ]


def test_rule_import_from_subscription_url_ignores_nodes(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(service, "_download", lambda _url, _user_agent="": b"""proxies:\n  - name: must-not-import\n    type: socks5\n    server: 127.0.0.1\n    port: 1080\nproxy-groups:\n  - name: PROXY\n    type: select\n    proxies: [DIRECT]\nrules:\n  - MATCH,PROXY\n""")
    service._initialized.clear()
    user = User(id="rule-import-user", username="rules", display_name="Rules")

    rule_set = service.import_rule_set({"name": "remote", "url": "https://example.invalid/sub"}, user)

    assert "groupName" not in rule_set
    assert rule_set["rules"] == ["MATCH,PROXY"]
    assert rule_set["groups"][0]["name"] == "PROXY"
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        assert conn.execute("SELECT COUNT(*) FROM csm_nodes").fetchone()[0] == 0



def test_rule_provider_library_crud_and_package(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="provider-user", username="provider", display_name="Provider")

    builtins = service.list_rule_providers(user)
    assert len(builtins) == 11
    assert {item["providerKey"] for item in builtins} >= {"ai-platforms", "google", "common-overseas", "ads", "china", "private-network", "microsoft", "apple", "steam"}
    assert all(item["builtin"] for item in builtins)

    selected = [next(item for item in builtins if item["providerKey"] == key)["id"] for key in ("ai-platforms", "google")]
    packaged = service.package_rule_providers(selected, "PROXY", user)
    assert list(packaged["providers"]) == ["ai-platforms", "google"]
    assert all("format" not in config for config in packaged["providers"].values())
    assert packaged["rules"] == ["RULE-SET,ai-platforms,PROXY", "RULE-SET,google,PROXY"]

    custom = service.save_rule_provider({
        "name": "公司网络",
        "providerKey": "company-network",
        "description": "公司域名和网段",
        "config": {"type": "manual", "behavior": "classical", "payload": ["DOMAIN-SUFFIX,example.com", "IP-CIDR,10.0.0.0/8,no-resolve"]},
    }, user)
    assert not custom["builtin"]
    assert custom["config"]["payload"] == ["DOMAIN-SUFFIX,example.com", "IP-CIDR,10.0.0.0/8,no-resolve"]
    custom_package = service.package_rule_providers([custom["id"]], "PROXY", user)
    assert custom_package["providers"] == {}
    assert custom_package["rules"] == [
        "DOMAIN-SUFFIX,example.com,PROXY",
        "IP-CIDR,10.0.0.0/8,PROXY,no-resolve",
    ]
    updated = service.save_rule_provider({**custom, "name": "公司规则"}, user, custom["id"])
    assert updated["name"] == "公司规则"

    with pytest.raises(ToolboxError) as conflict:
        service.save_rule_provider({
            "name": "重复 Key",
            "providerKey": "company-network",
            "config": {"type": "file", "behavior": "domain", "path": "./duplicate.yaml"},
        }, user)
    assert conflict.value.status_code == 409

    with pytest.raises(ToolboxError) as builtin_delete:
        service.delete_rule_provider(selected[0], user)
    assert builtin_delete.value.status_code == 409

    service.delete_rule_provider(custom["id"], user)
    assert all(item["id"] != custom["id"] for item in service.list_rule_providers(user))



def test_rule_subscription_is_cached_with_generated_hidden_key_and_packaged_inline(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_download",
        lambda _url, _user_agent="": b"payload:\n  - example.com\n  - +.example.org\n",
    )
    service._initialized.clear()
    user = User(id="cached-provider-user", username="cached-provider", display_name="Cached Provider")

    provider = service.save_rule_provider({
        "name": "远程规则",
        "providerKey": "",
        "config": {
            "type": "cached",
            "behavior": "domain",
            "url": "https://example.invalid/rules.yaml",
        },
    }, user)

    assert re.fullmatch(r"rp-[0-9a-f]{32}", provider["providerKey"])
    assert provider["config"]["type"] == "cached"
    assert provider["config"]["sourceUrl"] == "https://example.invalid/rules.yaml"
    assert provider["config"]["payload"] == ["example.com", "+.example.org"]
    assert "url" not in provider["config"]

    packaged = service.package_rule_providers([provider["id"]], "PROXY", user)
    assert packaged["providers"] == {}
    assert packaged["rules"] == [
        "DOMAIN,example.com,PROXY",
        "DOMAIN-SUFFIX,example.org,PROXY",
    ]

    original_copy = service.copy_rule_provider(provider["id"], "original", user)
    assert original_copy["id"] != provider["id"]
    assert original_copy["providerKey"] != provider["providerKey"]
    assert original_copy["name"] == "远程规则（副本）"
    assert original_copy["config"]["type"] == "cached"
    assert original_copy["config"]["sourceUrl"] == "https://example.invalid/rules.yaml"

    manual_copy = service.copy_rule_provider(provider["id"], "manual", user)
    assert manual_copy["providerKey"] not in {provider["providerKey"], original_copy["providerKey"]}
    assert manual_copy["name"] == "远程规则（副本 2）"
    assert manual_copy["config"] == {
        "type": "manual",
        "behavior": "domain",
        "payload": ["example.com", "+.example.org"],
    }


def test_rule_set_provider_bindings_are_resolved_live_without_snapshot(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="provider-binding-user", username="binding", display_name="Binding")

    provider = service.save_rule_provider({
        "name": "手写 AI 规则",
        "providerKey": "custom-ai",
        "config": {"type": "manual", "behavior": "domain", "payload": ["example.com"]},
    }, user)
    rule_set = service.save_rule_set({
        "name": "动态规则库",
        "groups": [{"name": "AI", "type": "select", "proxies": ["DIRECT"]}],
        "rules": ["RULE-SET,custom-ai,AI", "MATCH,AI"],
        "providers": {},
        "importMeta": {"providerBindings": {"AI": [provider["id"]]}, "strategyGroupEditor": True},
    }, user)
    assert rule_set["providers"] == {}

    profile = service.create_profile({"name": "动态配置", "ruleSetId": rule_set["id"]}, user)
    first_preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert "rule-providers" not in first_preview
    assert first_preview["rules"] == ["DOMAIN,example.com,AI", "MATCH,AI"]

    service.save_rule_provider({
        **provider,
        "config": {"type": "manual", "behavior": "domain", "payload": ["chat.example.com", "+.ai.example"]},
    }, user, provider["id"])
    second_preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert second_preview["rules"] == ["DOMAIN,chat.example.com,AI", "DOMAIN-SUFFIX,ai.example,AI", "MATCH,AI"]

    updated = service.save_rule_set({
        **rule_set,
        "importMeta": {"providerBindings": {"AI": []}, "strategyGroupEditor": True},
    }, user, rule_set["id"])
    assert updated["importMeta"]["providerBindings"] == {"AI": []}


def test_profile_rule_provider_output_mode_controls_inline_expansion(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="provider-output-mode-user", username="output-mode", display_name="Output Mode")
    downloads: list[str] = []

    def fake_download(url: str) -> list[str]:
        downloads.append(url)
        return ["chat.example.com", "+.ai.example"]

    monkeypatch.setattr(service, "_download_rule_provider_payload", fake_download)
    provider = service.save_rule_provider({
        "name": "AI 平台",
        "providerKey": "remote-test",
        "config": {
            "type": "http",
            "behavior": "domain",
            "interval": 86400,
            "url": "https://example.invalid/ai.yaml",
            "path": "./ruleset/remote-test.yaml",
        },
    }, user)
    rule_set = service.save_rule_set({
        "name": "输出模式规则",
        "groups": [{"name": "AI", "type": "select", "proxies": ["DIRECT"]}],
        "rules": ["MATCH,AI"],
        "providers": {},
        "importMeta": {"providerBindings": {"AI": [provider["id"]]}, "strategyGroupEditor": True},
    }, user)
    profile = service.create_profile({"name": "内联配置", "ruleSetId": rule_set["id"]}, user)
    assert profile["settings"]["ruleProviderOutputMode"] == "inline"

    inline_preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert downloads == ["https://example.invalid/ai.yaml"]
    assert "rule-providers" not in inline_preview
    assert "https://example.invalid/ai.yaml" not in inline_preview
    assert inline_preview["rules"] == [
        "DOMAIN,chat.example.com,AI",
        "DOMAIN-SUFFIX,ai.example,AI",
        "MATCH,AI",
    ]

    service.update_profile(profile["id"], {"settings": {"ruleProviderOutputMode": "url"}}, user)
    url_preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert downloads == ["https://example.invalid/ai.yaml"]
    assert url_preview["rule-providers"]["remote-test"]["url"] == "https://example.invalid/ai.yaml"
    assert url_preview["rules"] == ["RULE-SET,remote-test,AI", "MATCH,AI"]


def test_rule_import_moves_rule_providers_to_live_library(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="provider-import-user", username="provider-import", display_name="Provider Import")
    content = """proxy-groups:
  - name: PROXY
    type: select
    proxies: [DIRECT]
rule-providers:
  imported-domains:
    type: manual
    behavior: domain
    payload: [example.com]
rules:
  - RULE-SET,imported-domains,PROXY
  - MATCH,PROXY
"""

    rule_set = service.import_rule_set({"name": "导入规则", "content": content}, user)

    imported = next(item for item in service.list_rule_providers(user) if item["providerKey"] == "imported-domains")
    assert rule_set["providers"] == {}
    assert rule_set["importMeta"]["providerBindings"] == {"PROXY": [imported["id"]]}
    with pytest.raises(ToolboxError) as in_use:
        service.delete_rule_provider(imported["id"], user)
    assert in_use.value.status_code == 409


def test_custom_nodes_can_be_created_edited_and_subscription_nodes_copied(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="custom-node-user", username="custom-node", display_name="Custom Node")

    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    subscription_node = service.parse_uri("vless://00000000-0000-0000-0000-000000000001@example.org:443?security=tls&sni=example.org#source-node")
    assert subscription_node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [subscription_node])
    source_item = service.list_nodes(user)[0]
    assert not source_item["isCustom"]
    assert source_item["sources"] == ["source"]
    assert source_item["sourceIds"] == [source["id"]]

    with pytest.raises(ToolboxError) as read_only:
        service.update_custom_node(source_item["id"], "name: changed\ntype: vless\nserver: other.example\nport: 443\nuuid: test", user)
    assert read_only.value.status_code == 409

    copied = service.copy_subscription_node(source_item["id"], user)
    assert copied["isCustom"]
    assert copied["sources"] == []
    assert copied["sourceIds"] == []
    assert copied["stableIdentity"] != source_item["stableIdentity"]
    assert copied["name"].startswith("source-node（副本")
    assert copied["config"]["uuid"] == "00000000-0000-0000-0000-000000000001"

    stable_identity = copied["stableIdentity"]
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        conn.execute(
            """UPDATE csm_nodes SET resolved_ip='1.1.1.1',country_code='SG',
            country_label='新加坡',geo_checked_at=?,geo_error='' WHERE id=?""",
            (service._now(), copied["id"]),
        )
    updated = service.update_custom_node(copied["id"], """name: edited-node
type: trojan
server: trojan.example.org
port: 8443
password: new-secret
sni: trojan.example.org
""", user, alias="edited-alias")
    assert updated["stableIdentity"] == stable_identity
    assert updated["name"] == "edited-node"
    assert updated["protocol"] == "trojan"
    assert updated["alias"] == "edited-alias"
    assert updated["displayName"] == "edited-alias"
    assert updated["config"]["password"] == "new-secret"
    assert updated["resolvedIp"] == ""
    assert updated["country"] is None
    assert updated["geoCheckedAt"] is None

    created = service.create_custom_node("""name: hand-written
type: socks5
server: 127.0.0.1
port: 1080
username: local-user
password: local-pass
""", user, alias="local-proxy")
    assert created["isCustom"]
    assert created["protocol"] == "socks5"
    assert created["alias"] == "local-proxy"
    assert created["displayName"] == "local-proxy"

    with pytest.raises(ToolboxError) as cannot_delete_subscription:
        service.delete_custom_node(source_item["id"], user)
    assert cannot_delete_subscription.value.status_code == 409
    service.delete_custom_node(created["id"], user)
    assert all(node["id"] != created["id"] for node in service.list_nodes(user))


def test_node_geoip_resolution_cache_failures_and_region_groups(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="geoip-user", username="geoip", display_name="GeoIP")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)

    def _ss(fragment: str, server: str, port: int) -> dict:
        uri = "ss://" + base64.urlsafe_b64encode(f"aes-128-gcm:pass{port}@{server}:{port}".encode()).decode().rstrip("=") + f"#{fragment}"
        return service.parse_uri(uri)

    direct = _ss("direct", "8.8.8.8", 10001)
    domain_a = _ss("domain-a", "a.example", 10002)
    domain_b = _ss("domain-b", "b.example", 10003)
    failed = _ss("failed", "4.4.4.4", 10004)
    reserved = _ss("reserved", "192.168.1.10", 10005)
    assert all((direct, domain_a, domain_b, failed, reserved))
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [direct, domain_a, domain_b, failed, reserved])
        rows = {row["name"]: row for row in conn.execute("SELECT * FROM csm_nodes")}
        conn.executemany(
            "INSERT INTO csm_probe_results(id,node_id,reachable,dns_address,latency_ms,error,created_at) VALUES(?,?,1,?,10,'',?)",
            [(service._id(), rows["domain-a"]["id"], "1.1.1.1", service._now()),
             (service._id(), rows["domain-b"]["id"], "1.1.1.1", service._now())],
        )

    queried_ips: list[str] = []
    results = {
        "8.8.8.8": {"countryCode": "US", "countryLabel": "美国", "error": ""},
        "1.1.1.1": {"countryCode": "SG", "countryLabel": "新加坡", "error": ""},
        "4.4.4.4": {"countryCode": "", "countryLabel": "", "error": "IP.SB 查询失败"},
    }
    def fake_query(ip: str) -> dict[str, str]:
        queried_ips.append(ip)
        return results[ip]

    monkeypatch.setattr(service, "_query_ip_sb", fake_query)
    updated = service.refresh_node_geoip([], user)
    updated_by_name = {node["name"]: node for node in updated}
    assert updated_by_name["direct"]["country"] == "US"
    assert updated_by_name["direct"]["countryLabel"] == "美国"
    assert service._normalise_ip_sb_payload({"country_code": "US", "country": "United States"}) == {
        "countryCode": "US", "countryLabel": "美国", "error": ""
    }
    assert updated_by_name["domain-a"]["country"] == "SG"
    assert updated_by_name["domain-b"]["country"] == "SG"
    assert updated_by_name["failed"]["country"] is None
    assert updated_by_name["failed"]["geoError"] == "IP.SB 查询失败"
    assert "保留地址" in updated_by_name["reserved"]["geoError"]
    # Duplicate IPs use the persistent cache; a failed lookup is still cached.
    assert set(queried_ips) == {"8.8.8.8", "1.1.1.1", "4.4.4.4"}

    region = service.save_node_group({"name": "US/SG", "kind": "region", "config": {"countries": ["US", "SG"]}}, user)
    assert set(region["members"]) == {"direct", "domain-a", "domain-b"}

    # A changed probe IP invalidates the old node-level GeoIP result.
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        node_id = conn.execute("SELECT id FROM csm_nodes WHERE name='domain-a'").fetchone()["id"]
        conn.execute("INSERT INTO csm_probe_results(id,node_id,reachable,dns_address,latency_ms,error,created_at) VALUES(?,?,1,?,9,'',?)",
                     (service._id(), node_id, "9.9.9.9", service._now()))
    results["9.9.9.9"] = {"countryCode": "JP", "countryLabel": "日本", "error": ""}
    changed = {node["name"]: node for node in service.refresh_node_geoip([node_id], user)}
    assert changed["domain-a"]["country"] == "JP"
    assert changed["domain-a"]["resolvedIp"] == "9.9.9.9"
    assert queried_ips[-1] == "9.9.9.9"


def test_node_geoip_resolves_domain_when_probe_contains_reserved_fake_ip(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="geoip-dns-user", username="geoip-dns", display_name="GeoIP DNS")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    node = service.parse_uri("ss://" + base64.urlsafe_b64encode(b"aes-128-gcm:pass@dns.example:443").decode().rstrip("=") + "#dns-node")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
        node_id = conn.execute("SELECT id FROM csm_nodes WHERE name='dns-node'").fetchone()["id"]
        conn.execute(
            "INSERT INTO csm_probe_results(id,node_id,reachable,dns_address,latency_ms,error,created_at) VALUES(?,?,1,?,10,'',?)",
            (service._id(), node_id, "198.18.0.1", service._now()),
        )

    monkeypatch.setattr(service.socket, "getaddrinfo", lambda host, port, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(service, "_query_ip_sb", lambda ip: {"countryCode": "US", "countryLabel": "美国", "error": ""})
    result = service.refresh_node_geoip([], user)[0]
    assert result["resolvedIp"] == "93.184.216.34"
    assert result["country"] == "US"
    assert result["countryLabel"] == "美国"


def test_node_groups_crud_resolution_and_strategy_expansion(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="group-user", username="groups", display_name="Groups")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    def _ss(fragment: str, port: int) -> dict:
        uri = "ss://" + base64.urlsafe_b64encode(f"aes-128-gcm:pass{port}@example.org:{port}".encode()).decode().rstrip("=") + f"#{fragment}"
        return service.parse_uri(uri)
    node_a = _ss("🇭🇰 Hong Kong 01", 10001)
    node_b = _ss("🇯🇵 Japan 01", 10002)
    node_c = _ss("🇺🇸 US 01", 10003)
    assert node_a and node_b and node_c
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node_a, node_b, node_c])

    # Region membership is now based on persisted GeoIP results, never node names.
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        geo_by_name = {
            "🇭🇰 Hong Kong 01": ("203.0.114.1", "HK", "香港"),
            "🇯🇵 Japan 01": ("203.0.114.2", "JP", "日本"),
            "🇺🇸 US 01": ("203.0.114.3", "US", "美国"),
        }
        for name, (ip, code, label) in geo_by_name.items():
            conn.execute("""UPDATE csm_nodes SET resolved_ip=?,country_code=?,country_label=?,
              geo_checked_at=?,geo_error='' WHERE name=?""", (ip, code, label, service._now(), name))

    nodes = service.list_nodes(user)
    ids_by_name = {node["name"]: node["id"] for node in nodes}

    # Region group matches multiple countries, optionally scoped to explicit nodes.
    region = service.save_node_group({"name": "亚洲节点", "kind": "region",
                                      "config": {"countries": ["HK", "JP"], "nodeIds": []}}, user)
    assert region["kind"] == "region"
    assert set(region["members"]) == {"🇭🇰 Hong Kong 01", "🇯🇵 Japan 01"}
    region_scoped = service.save_node_group({"name": "仅日本", "kind": "region",
                                             "config": {"countries": ["HK", "JP"],
                                                        "nodeIds": [ids_by_name["🇯🇵 Japan 01"]]}}, user)
    assert region_scoped["members"] == ["🇯🇵 Japan 01"]

    # Latency group picks the fastest reachable nodes, optionally scoped to explicit nodes.
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        for node, latency in ((node_a, 80), (node_b, 40), (node_c, 190)):
            row = conn.execute("SELECT id FROM csm_nodes WHERE fingerprint=?", (node["fingerprint"],)).fetchone()
            conn.execute("INSERT INTO csm_probe_results(id,node_id,reachable,latency_ms,error,created_at) VALUES(?,?,1,?,?,?)",
                         (service._id(), row["id"], latency, "", service._now()))
    latency = service.save_node_group({"name": "前2快", "kind": "latency",
                                       "config": {"mode": "top", "count": 2, "nodeIds": []}}, user)
    assert latency["members"] == ["🇯🇵 Japan 01", "🇭🇰 Hong Kong 01"]
    latency_scoped = service.save_node_group({"name": "延迟阈值内", "kind": "latency",
                                              "config": {"mode": "threshold", "thresholdMs": 100,
                                                         "nodeIds": [ids_by_name["🇭🇰 Hong Kong 01"], ids_by_name["🇺🇸 US 01"]]}}, user)
    assert latency_scoped["members"] == ["🇭🇰 Hong Kong 01"]

    # Custom group keeps the exact picked nodes.
    custom = service.save_node_group({"name": "自选", "kind": "custom",
                                      "config": {"nodeIds": [ids_by_name["🇺🇸 US 01"]]}}, user)
    assert custom["members"] == ["🇺🇸 US 01"]

    # A strategy group referencing node groups is expanded into concrete proxies.
    rule_set = service.save_rule_set({
        "name": "grouped rules",
        "groups": [{"name": "PROXY", "type": "select", "proxies": ["DIRECT"],
                    "nodeGroups": [region["id"], latency["id"], custom["id"]]}],
        "rules": ["MATCH,PROXY"],
        "providers": {},
    }, user)
    profile = service.create_profile({"name": "grouped profile", "ruleSetId": rule_set["id"]}, user)
    preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    proxies = set(preview["proxy-groups"][0]["proxies"])
    assert proxies == {"DIRECT", "🇭🇰 Hong Kong 01", "🇯🇵 Japan 01", "🇺🇸 US 01"}

def test_subscription_details_html_layout() -> None:
    from tools.airplay_subscription_manager.backend.router import _subscription_details_html

    page = _subscription_details_html("token", {
        "name": "visual sub", "ruleSetName": "basic rules", "publishedAt": "2026-01-01T00:00:00+00:00",
        "contentHash": "hash", "mode": "rule", "yaml": "mode: rule",
        "proxies": [{"name": "node", "type": "vless", "server": "example.org", "port": 443,
                     "shareUri": "vless://uuid@example.org:443#node",
                     "latencyMs": 123, "reachable": True, "checkedAt": "2026-01-01T00:01:00+00:00"}],
        "groups": [{"name": "PROXY", "type": "select", "proxies": ["node"],
                    "providers": [{"name": "示例域名", "kind": "自定义", "behavior": "domain",
                                   "ruleCount": 1, "payload": ["example.com"]}]}],
        "requestRuns": [{"requestedAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                         "clientIp": "127.0.0.1", "userAgent": "pytest", "statusCode": 200}],
    })
    assert "订阅拉取日志" in page
    assert '<dialog class="modal"' in page
    assert '<details ' not in page
    assert '发布时间' not in page
    assert '内容校验' not in page
    assert '复制订阅链接' in page
    assert '下载 AirPlay YAML' in page
    assert '扫码导入 YAML 订阅' not in page
    assert 'data:image/svg+xml;base64,' in page
    assert 'data-copy-node-link' in page
    assert '显示节点二维码' in page
    assert 'speed-dot good' in page
    assert '最近拉取' in page
    assert '5 分钟前' in page
    assert "123 ms" in page
    assert "example.com" in page
    assert "Rule Provider" not in page
    assert "<h2>规则</h2>" not in page
