from __future__ import annotations

import base64
import json

from backend.app.core.config import Settings
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
    node = service.parse_uri("vless://00000000-0000-0000-0000-000000000001@example.org:443?security=tls#vless")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
    profile = service.create_profile({"name": "my sub", "targetKernel": "mihomo"}, user)
    service.set_profile_selections(profile["id"], [node["stable_identity"]], user)
    published = service.publish_profile(profile["id"], user)
    result = service.public_subscription(published["subscriptionToken"])
    assert result is not None
    assert "vless" in result[0]
    rotated = service.rotate_profile_token(profile["id"], user)
    assert service.public_subscription(published["subscriptionToken"]) is None
    assert service.public_subscription(rotated["subscriptionToken"]) is not None


def test_legacy_clash_rejects_mihomo_only_node(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    user = User(id="user-2", username="two", display_name="Two")
    source = service.create_source({"name": "source", "url": "https://example.invalid/sub"}, user)
    node = service.parse_uri("hysteria2://password@example.org:443#hy2")
    assert node
    with database.user_tool_connection_context(user.id, service.TOOL_ID) as conn:
        service._store_nodes(conn, source["id"], [node])
    profile = service.create_profile({"name": "legacy", "targetKernel": "clash"}, user)
    service.set_profile_selections(profile["id"], [node["stable_identity"]], user)
    result = service.validate_profile(profile["id"], user)
    assert not result["valid"]
    assert any(message["code"] == "KERNEL_INCOMPATIBLE" for message in result["messages"])
