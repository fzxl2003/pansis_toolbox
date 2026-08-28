from __future__ import annotations

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.db import database
from backend.app.services import ssh_server_service
from backend.app.services.auth_service import User


@pytest.fixture(autouse=True)
def isolated_platform_db(monkeypatch, tmp_path):
    settings = database.get_settings()
    monkeypatch.setattr(settings, "platform_db_path", tmp_path / "platform.db")


def user(user_id: str, role: str = "user") -> User:
    return User(id=user_id, username=user_id, display_name=user_id, role=role, disabled=False)


def add_user(user_id: str) -> None:
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO users (id,username,display_name,password_hash,password_salt,role,disabled,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, user_id, user_id, "hash", "salt", "user", 0, "now"),
        )


def payload(**overrides):
    return {
        "name": "gpu-a", "host": "10.0.0.1", "port": 22, "sshUsername": "ubuntu",
        "authType": "password", "sshPassword": "secret", "privateKey": "", "privateKeyPassphrase": "",
        "isPublic": False, "allowedUserIds": [], **overrides,
    }


def test_private_server_is_visible_only_to_owner():
    database.init_database(); add_user("owner"); add_user("other")
    saved = ssh_server_service.create_server(payload(), user("owner", "admin"))
    assert [item["id"] for item in ssh_server_service.list_servers(user("owner", "admin"))] == [saved["id"]]
    assert ssh_server_service.list_servers(user("other")) == []
    with pytest.raises(ToolboxError, match="没有该 SSH 服务器"):
        ssh_server_service.get_server_credentials(saved["id"], user("other"))


def test_admin_public_server_uses_explicit_audience_and_hides_credentials():
    database.init_database(); add_user("admin"); add_user("reader"); add_user("blocked")
    saved = ssh_server_service.create_server(payload(isPublic=True, allowedUserIds=["reader"]), user("admin", "admin"))
    visible = ssh_server_service.list_servers(user("reader"))
    assert visible[0]["id"] == saved["id"]
    assert "sshPassword" not in visible[0] and "privateKey" not in visible[0]
    assert ssh_server_service.list_servers(user("blocked")) == []


def test_regular_user_cannot_configure_but_can_use_authorized_key_server():
    database.init_database(); add_user("admin"); add_user("user")
    with pytest.raises(ToolboxError, match="仅管理员"):
        ssh_server_service.create_server(payload(isPublic=True), user("user"))
    saved = ssh_server_service.create_server(
        payload(authType="private_key", sshPassword="", privateKey="key", isPublic=True, allowedUserIds=["user"]),
        user("admin", "admin"),
    )
    credentials = ssh_server_service.get_server_credentials(saved["id"], user("user"))
    assert credentials["auth_type"] == "private_key"
    assert credentials["private_key"] == "key"


def test_legacy_migration_preserves_id_owner_and_avoids_duplicate_names():
    database.init_database(); add_user("owner"); add_user("admin")
    ssh_server_service.create_server(payload(name="gpu-a"), user("admin", "admin"))
    migrated = ssh_server_service.migrate_legacy_server(
        server_id="old-tool-id", owner=user("owner"), name="gpu-a", host="10.0.0.2", port=2222,
        ssh_username="ubuntu", ssh_password="old-secret", source_tool="experiment_monitor",
    )
    assert migrated["id"] == "old-tool-id"
    assert migrated["name"] != "gpu-a"
    assert ssh_server_service.get_server_credentials("old-tool-id", user("owner"))["ssh_password"] == "old-secret"


def test_shared_legacy_migration_preserves_existing_default_server_access():
    database.init_database(); add_user("owner"); add_user("reader")
    ssh_server_service.migrate_legacy_server(
        server_id="old-default-id", owner=user("owner"), name="legacy-default", host="10.0.0.3", port=22,
        ssh_username="ubuntu", ssh_password="old-secret", source_tool="server_monitor",
        is_public=True, allowed_user_ids=["owner", "reader"],
    )
    assert ssh_server_service.get_server_credentials("old-default-id", user("reader"))["ssh_password"] == "old-secret"
