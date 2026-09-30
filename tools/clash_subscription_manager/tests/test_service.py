from __future__ import annotations

import base64
import json
import re

import pytest
import yaml

from backend.app.core.config import Settings
from backend.app.core.errors import ToolboxError
from backend.app.db import database
from backend.app.services.auth_service import User
from tools.clash_subscription_manager.backend import service


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
    profile = service.create_profile({"name": "my sub"}, user)
    assert "targetKernel" not in profile
    service.set_profile_selections(profile["id"], [node["stable_identity"]], user)
    published = service.publish_profile(profile["id"], user)
    result = service.public_subscription(published["subscriptionToken"])
    assert result is not None
    assert "vless" in result[0]
    assert "reality-opts" in result[0]
    rotated = service.rotate_profile_token(profile["id"], user)
    assert service.public_subscription(published["subscriptionToken"]) is None
    assert service.public_subscription(rotated["subscriptionToken"]) is not None


def test_clash_meta_output_rejects_unknown_node(tmp_path, monkeypatch) -> None:
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
    profile = service.create_profile({"name": "compatible"}, user)
    service.set_profile_selections(profile["id"], [node["stable_identity"]], user)
    result = service.validate_profile(profile["id"], user)
    assert not result["valid"]
    assert any(message["code"] == "INCOMPATIBLE_OUTPUT" for message in result["messages"])


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
    service.set_profile_selections(profile["id"], [node["stable_identity"]], user)

    validation = service.validate_profile(profile["id"], user)
    assert validation["valid"]
    preview = yaml.safe_load(service.preview_profile(profile["id"], user)["yaml"])
    assert preview["proxies"][0]["name"] == "friendly-name"
    assert preview["proxy-groups"][0]["proxies"] == ["friendly-name", "friendly-name"]
    assert preview["rules"] == [
        "DOMAIN,example.com,friendly-name",
        "IP-CIDR,10.0.0.0/8,friendly-name,no-resolve",
        "MATCH,friendly-name",
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
    assert len(builtins) == 5
    assert {item["providerKey"] for item in builtins} >= {"ai-platforms", "google", "common-overseas"}
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
