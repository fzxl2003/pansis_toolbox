from __future__ import annotations

from typing import Any

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, init_database
from backend.app.services.auth_service import User
from tools.docker_manager.backend import service


SERVER_A = "dm_test_server_a"
SERVER_B = "dm_test_server_b"
USER_A = "dm_test_user_a"
USER_B = "dm_test_user_b"


class FakeSshClient:
    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def docker_manager_test_data() -> None:
    init_database()
    service.init_docker_database()
    _cleanup()
    with get_connection() as conn:
        for uid, username in ((USER_A, "dm_user_a"), (USER_B, "dm_user_b")):
            conn.execute(
                """
                INSERT INTO users (id, username, display_name, password_hash, password_salt, role, disabled, created_at)
                VALUES (?, ?, ?, 'hash', 'salt', 'user', 0, ?)
                """,
                (uid, username, username, service._now()),
            )
        for sid, name in ((SERVER_A, "Docker Test A"), (SERVER_B, "Docker Test B")):
            conn.execute(
                """
                INSERT INTO docker_servers
                    (id, name, host, port, ssh_username, ssh_password_encrypted, created_by, created_at, updated_at)
                VALUES (?, ?, '127.0.0.1', 22, 'docker', ?, ?, ?, ?)
                """,
                (sid, name, service._encrypt("pw"), USER_A, service._now(), service._now()),
            )
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as conn:
        for table, column in (
            ("docker_df_cache", "server_id"),
            ("docker_df_images", "server_id"),
            ("docker_df_containers", "server_id"),
            ("docker_df_volumes", "server_id"),
            ("docker_container_resource_cache", "server_id"),
            ("docker_resource_roles", "server_id"),
            ("docker_user_perms", "server_id"),
            ("docker_images_meta", "server_id"),
            ("docker_containers_meta", "server_id"),
            ("docker_volumes_meta", "server_id"),
            ("docker_servers", "id"),
        ):
            conn.execute(f"DELETE FROM {table} WHERE {column} IN (?, ?)", (SERVER_A, SERVER_B))
        conn.execute("DELETE FROM users WHERE id IN (?, ?)", (USER_A, USER_B))


def _user(uid: str = USER_A) -> User:
    return User(id=uid, username=uid, display_name=uid, role="user")


def _admin() -> User:
    return User(id="admin", username="admin", display_name="Admin", role="admin")


def _set_perms(server_id: str, user_id: str, **overrides: Any) -> None:
    perms = dict(service._PERMS_DEFAULTS)
    perms.update(overrides)
    service.set_user_perms(server_id, user_id, perms, User(id="admin", username="admin", display_name="Admin", role="admin"))


DF_SAMPLE = """
Images space usage:

REPOSITORY          TAG       IMAGE ID       CREATED        SIZE      SHARED SIZE   UNIQUE SIZE   CONTAINERS
nginx               latest    abcdef123456   2 weeks ago    187MB     0B            187MB         1
private/app         1         deadbeef0000   3 days ago     1.2GB     100MB         1.1GB         0

Containers space usage:

CONTAINER ID   IMAGE        COMMAND                  LOCAL VOLUMES   SIZE      CREATED        STATUS        NAMES
abc123def456   nginx        "nginx -g daemon off;"   1               12.3MB    2 hours ago    Up 2 hours    web

Local Volumes space usage:

VOLUME NAME   LINKS     SIZE
dataset       1         2.5GB
"""


def test_docker_df_refresh_populates_cache_and_lists_without_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd == "docker system df -v":
            return DF_SAMPLE, "", 0
        if cmd.startswith("docker ps -a --format"):
            return "abc123def456\tweb\t0.0.0.0:8080->80/tcp, :::8080->80/tcp\n", "", 0
        return "", f"unexpected command: {cmd}", 1

    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    result = service.refresh_docker_df_cache(SERVER_A, _admin())

    assert result["images"] == 2
    assert result["containers"] == 1
    assert result["volumes"] == 1

    def fake_exec_ports_failed(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd == "docker system df -v":
            return DF_SAMPLE, "", 0
        if cmd.startswith("docker ps -a --format"):
            return "", "ports unavailable", 1
        return "", f"unexpected command: {cmd}", 1

    monkeypatch.setattr(service, "_ssh_exec", fake_exec_ports_failed)
    service.refresh_docker_df_cache(SERVER_A, _admin())

    def fail_connect(row: Any) -> FakeSshClient:
        raise AssertionError("list calls should read docker system df cache without SSH")

    monkeypatch.setattr(service, "_ssh_connect", fail_connect)

    images = service.list_images(SERVER_A, _admin())
    containers = service.list_containers(SERVER_A, _admin())
    volumes = service.list_volumes(SERVER_A, _admin())["volumes"]

    assert [(img["repo"], img["tag"], img["containers"]) for img in images] == [
        ("nginx", "latest", 1),
        ("private/app", "1", 0),
    ]
    assert containers[0]["Names"] == "web"
    assert containers[0]["Image"] == "nginx"
    assert containers[0]["Ports"] == "0.0.0.0:8080->80/tcp, :::8080->80/tcp"
    assert volumes[0]["name"] == "dataset"
    assert volumes[0]["links"] == 1
    assert volumes[0]["sizeGb"] == pytest.approx(2.5)


def test_resource_lists_require_server_visibility() -> None:
    _set_perms(SERVER_A, USER_A, img_use=True, vol_use=True, ctr_use=True)

    with pytest.raises(ToolboxError) as image_exc:
        service.list_images(SERVER_A, _user())
    assert image_exc.value.status_code == 403

    with pytest.raises(ToolboxError) as volume_exc:
        service.list_volumes(SERVER_A, _user())
    assert volume_exc.value.status_code == 403

    with pytest.raises(ToolboxError) as container_exc:
        service.list_containers(SERVER_A, _user())
    assert container_exc.value.status_code == 403


def test_volume_list_does_not_treat_container_view_all_as_volume_view_all() -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, vol_use=True, ctr_view_all=True)
    service._store_docker_df_cache(
        SERVER_A,
        "",
        {
            "refreshedAt": service._now(),
            "images": [],
            "containers": [],
            "volumes": [
                {"name": "owned-data", "links": 1, "size": "1GB", "sizeGb": 1.0},
                {"name": "other-data", "links": 0, "size": "2GB", "sizeGb": 2.0},
            ],
        },
    )
    with get_connection() as conn:
        service._record_resource_creator(conn, SERVER_A, "volume", "owned-data", USER_A)
        service._record_resource_creator(conn, SERVER_A, "volume", "other-data", USER_B)

    visible = service.list_volumes(SERVER_A, _user(USER_A))["volumes"]

    assert [v["name"] for v in visible] == ["owned-data"]

    _set_perms(SERVER_A, USER_A, server_visible=True, vol_use=True, vol_view_all=True, ctr_view_all=True)
    visible_all = service.list_volumes(SERVER_A, _user(USER_A))["volumes"]
    assert [v["name"] for v in visible_all] == ["other-data", "owned-data"]


def test_pull_image_records_creator_owner_and_quota_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, img_pull=True, img_use=True)
    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", lambda client, cmd, timeout=60: ("pulled", "", 0))

    result = service.pull_image(SERVER_A, "nginx", _user())
    assert result["success"] is True

    with get_connection() as conn:
        roles = {
            row["role"]
            for row in conn.execute(
                """SELECT role FROM docker_resource_roles
                   WHERE server_id=? AND resource_type='image' AND resource_ref='nginx:latest' AND user_id=?""",
                (SERVER_A, USER_A),
            ).fetchall()
        }
        meta = conn.execute(
            "SELECT owner_user_id FROM docker_images_meta WHERE server_id=? AND image_ref='nginx:latest'",
            (SERVER_A,),
        ).fetchone()
    assert roles == {"creator", "owner", "quota_holder"}
    assert meta["owner_user_id"] == USER_A


def test_copy_image_requires_source_resource_access() -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, img_copy=True, img_use=True)
    _set_perms(SERVER_B, USER_A, server_visible=True, img_copy=True, img_use=True)

    with pytest.raises(ToolboxError) as exc:
        service.copy_image(SERVER_A, SERVER_B, "private/image:1", _user())

    assert exc.value.status_code == 403
    assert "源镜像" in exc.value.message


def test_remove_container_cleans_name_and_short_id_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, ctr_use=True)
    with get_connection() as conn:
        service._record_resource_creator(conn, SERVER_A, "container", "trainer", USER_A)
        service._record_resource_creator(conn, SERVER_A, "container", "abcdef123456", USER_A)

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd.startswith("docker inspect --format"):
            return "/trainer\tabcdef1234567890\n", "", 0
        if cmd.startswith("docker rm -f"):
            return "trainer\n", "", 0
        return "", "", 1

    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    result = service.container_action(SERVER_A, "trainer", "remove", _user())
    assert result["success"] is True

    with get_connection() as conn:
        count = conn.execute(
            """SELECT COUNT(*) AS c FROM docker_resource_roles
               WHERE server_id=? AND resource_type='container' AND resource_ref IN ('trainer', 'abcdef123456')""",
            (SERVER_A,),
        ).fetchone()["c"]
        legacy_count = conn.execute(
            "SELECT COUNT(*) AS c FROM docker_containers_meta WHERE server_id=? AND container_ref IN ('trainer', 'abcdef123456')",
            (SERVER_A,),
        ).fetchone()["c"]
    assert count == 0
    assert legacy_count == 0


def test_volume_detail_enforces_access_and_returns_quota_holders(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, vol_use=True)
    _set_perms(SERVER_A, USER_B, server_visible=True, vol_use=True)
    admin = User(id="admin", username="admin", display_name="Admin", role="admin")
    service.assign_resource_roles(
        SERVER_A,
        "volume",
        "dataset",
        [USER_A],
        [],
        USER_A,
        admin,
        quota_holder_user_ids=[USER_A, USER_B],
    )
    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", lambda client, cmd, timeout=60: ("", "", 0))

    detail = service.get_volume_detail(SERVER_A, "dataset", _user(USER_A))
    assert detail["roles"]["quotaHolderUserIds"] == [USER_A, USER_B]
    assert [u["userId"] for u in detail["roles"]["quotaHolders"]] == [USER_A, USER_B]

    with pytest.raises(ToolboxError) as exc:
        service.get_volume_detail(SERVER_A, "dataset", _user(USER_B))
    assert exc.value.status_code == 403


def test_create_container_requires_image_access(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, ctr_create=True, img_use=True)

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd.startswith("docker ps -a --format"):
            return "", "", 0
        if cmd.startswith("docker image inspect"):
            return "", "", 0
        return "", "", 0

    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    with pytest.raises(ToolboxError) as exc:
        service.create_container_run(SERVER_A, {"image": "private/app:1", "name": "app"}, _user())

    assert exc.value.status_code == 403
    assert "镜像 private/app:1" in exc.value.message


def test_create_container_uses_accessible_image_and_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, ctr_create=True, img_use=True, vol_use=True)
    with get_connection() as conn:
        service._record_resource_creator(conn, SERVER_A, "image", "private/app:1", USER_A)
        service._record_resource_creator(conn, SERVER_A, "volume", "dataset", USER_A)

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd.startswith("docker ps -a --format"):
            return "", "", 0
        if cmd.startswith("docker image inspect private/app:1"):
            return "", "", 0
        if cmd.startswith("docker volume inspect dataset"):
            return "", "", 0
        if cmd.startswith("docker run"):
            return "abcdef1234567890\n", "", 0
        return "", "", 0

    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    result = service.create_container_run(
        SERVER_A,
        {"image": "private/app:1", "name": "trainer", "volumes": ["dataset:/data"]},
        _user(),
    )

    assert result["success"] is True
    with get_connection() as conn:
        row = conn.execute(
            """SELECT 1 FROM docker_resource_roles
               WHERE server_id=? AND resource_type='container' AND resource_ref='trainer' AND user_id=? AND role='owner'""",
            (SERVER_A, USER_A),
        ).fetchone()
    assert row is not None


def test_create_container_rejects_missing_named_volume_without_create_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(SERVER_A, USER_A, server_visible=True, ctr_create=True, img_use=True, vol_use=True)
    with get_connection() as conn:
        service._record_resource_creator(conn, SERVER_A, "image", "private/app:1", USER_A)

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd.startswith("docker ps -a --format"):
            return "", "", 0
        if cmd.startswith("docker image inspect private/app:1"):
            return "", "", 0
        if cmd.startswith("docker volume inspect scratch"):
            return "", "", 1
        return "", "", 0

    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    with pytest.raises(ToolboxError) as exc:
        service.create_container_run(
            SERVER_A,
            {"image": "private/app:1", "name": "trainer", "volumes": ["scratch:/tmp"]},
            _user(),
        )

    assert exc.value.status_code == 403
    assert "创建卷" in exc.value.message


def test_create_container_records_auto_pulled_image_and_created_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_perms(
        SERVER_A,
        USER_A,
        server_visible=True,
        ctr_create=True,
        img_use=True,
        img_pull=True,
        vol_create=True,
        vol_use=True,
    )

    def fake_exec(client: FakeSshClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        if cmd.startswith("docker ps -a --format"):
            return "", "", 0
        if cmd.startswith("docker images --format"):
            return "", "", 0
        if cmd.startswith("docker image inspect new/app:2"):
            return "", "", 1
        if cmd.startswith("docker volume inspect scratch"):
            return "", "", 1
        if cmd.startswith("docker run"):
            return "123456abcdef7890\n", "", 0
        return "", "", 0

    monkeypatch.setattr(service, "_ssh_connect", lambda row: FakeSshClient())
    monkeypatch.setattr(service, "_ssh_exec", fake_exec)

    result = service.create_container_run(
        SERVER_A,
        {"image": "new/app:2", "name": "worker", "volumes": ["scratch:/work"]},
        _user(),
    )

    assert result["success"] is True
    with get_connection() as conn:
        image_owner = conn.execute(
            "SELECT owner_user_id FROM docker_images_meta WHERE server_id=? AND image_ref='new/app:2'",
            (SERVER_A,),
        ).fetchone()
        volume_owner = conn.execute(
            "SELECT owner_user_id FROM docker_volumes_meta WHERE server_id=? AND volume_name='scratch'",
            (SERVER_A,),
        ).fetchone()
    assert image_owner["owner_user_id"] == USER_A
    assert volume_owner["owner_user_id"] == USER_A
