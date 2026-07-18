import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.db.database import (
    get_connection,
    list_user_tool_dbs,
    user_tool_connection_context,
)
from backend.app.main import app
from backend.app.registry.loader import discover_tools
from backend.app.registry.models import ToolStatus
from backend.app.services.auth_service import User
from tools.experiment_monitor.backend import service as experiment_service
from tools.server_monitor.backend import service as monitor_service


client = TestClient(app)
EXPERIMENT_USER = User(id="experiment_test_user", username="experiment_user", display_name="Experiment User")


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
    assert any(tool.tool_id == "server_monitor" and tool.status == ToolStatus.available for tool in tools)


def test_auth_me_anonymous() -> None:
    client.cookies.clear()
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
    assert me_response.json()["user"]["role"] == "admin"

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


def test_admin_can_manage_users() -> None:
    _cleanup_monitor_test_data()
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    username = "monitor_user_test"
    created = client.post(
        "/api/auth/users",
        json={"username": username, "displayName": "Monitor User", "password": "pw123", "role": "user"},
        cookies=cookies,
    )
    if created.status_code == 409:
        users = client.get("/api/auth/users", cookies=cookies).json()["users"]
        user = next(item for item in users if item["username"] == username)
    else:
        assert created.status_code == 200
        user = created.json()["user"]
    assert user["role"] == "user"

    disabled = client.post(f"/api/auth/users/{user['id']}/disabled", json={"disabled": True}, cookies=cookies)
    assert disabled.status_code == 200
    assert disabled.json()["user"]["disabled"] is True

    enabled = client.post(f"/api/auth/users/{user['id']}/disabled", json={"disabled": False}, cookies=cookies)
    assert enabled.status_code == 200
    assert enabled.json()["user"]["disabled"] is False
    _cleanup_monitor_test_data()


def test_user_can_change_own_password() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    username = f"password_user_{uuid4().hex[:8]}"
    created = client.post(
        "/api/auth/users",
        json={"username": username, "displayName": "Password User", "password": "old-pass", "role": "user"},
        cookies=cookies,
    )
    assert created.status_code == 200
    user = created.json()["user"]

    login_response = client.post("/api/auth/login", json={"username": username, "password": "old-pass"})
    assert login_response.status_code == 200
    changed = client.post(
        "/api/auth/password",
        json={"currentPassword": "old-pass", "newPassword": "new-pass"},
        cookies=login_response.cookies,
    )
    assert changed.status_code == 200
    assert changed.json()["sessionsRevoked"] is True

    old_login = client.post("/api/auth/login", json={"username": username, "password": "old-pass"})
    assert old_login.status_code == 401
    new_login = client.post("/api/auth/login", json={"username": username, "password": "new-pass"})
    assert new_login.status_code == 200

    deleted = client.delete(f"/api/auth/users/{user['id']}", cookies=cookies)
    assert deleted.status_code == 200


def test_tool_access_can_hide_tool_from_anonymous_users() -> None:
    _reset_tool_access("text_cleaner")
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies

    updated = client.post(
        "/api/tools-admin/text_cleaner/access",
        json={"globalPublic": False, "allowedUserIds": []},
        cookies=cookies,
    )
    assert updated.status_code == 200
    assert updated.json()["globalPublic"] is False

    client.cookies.clear()
    tools = client.get("/api/tools")
    assert tools.status_code == 200
    assert all(tool["id"] != "text_cleaner" for tool in tools.json())

    detail = client.get("/api/tools/text_cleaner")
    assert detail.status_code == 401
    assert detail.json()["error"]["code"] == "LOGIN_REQUIRED"

    clean = client.post(
        "/api/tools/text-cleaner/clean",
        json={"text": " hello ", "trim": True, "collapseWhitespace": True, "removeBlankLines": True},
    )
    assert clean.status_code == 401

    restored = client.post(
        "/api/tools-admin/text_cleaner/access",
        json={"globalPublic": True, "allowedUserIds": []},
        cookies=cookies,
    )
    assert restored.status_code == 200
    _reset_tool_access("text_cleaner")


def test_admin_can_clear_one_tool_storage() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    storage_dir = get_settings().storage_dir
    shared_dir = storage_dir / "data" / "tools" / "memo_demo"
    user_dir = storage_dir / "user_data" / "clear_test_user" / "tools" / "memo_demo"
    shared_dir.mkdir(parents=True, exist_ok=True)
    user_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "sample.txt").write_text("shared", encoding="utf-8")
    (user_dir / "sample.txt").write_text("user", encoding="utf-8")
    with get_connection() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS memo_demo_clear_test (id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE IF NOT EXISTS unrelated_clear_test (id TEXT PRIMARY KEY)")
        connection.commit()

    cleared = client.delete("/api/tools-admin/memo_demo/storage", cookies=cookies)
    assert cleared.status_code == 200
    payload = cleared.json()
    assert "memo_demo_clear_test" in payload["droppedTables"]
    assert not shared_dir.exists()
    assert not user_dir.exists()

    with get_connection() as connection:
        memo_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memo_demo_clear_test'"
        ).fetchone()
        unrelated_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'unrelated_clear_test'"
        ).fetchone()
        connection.execute("DROP TABLE IF EXISTS unrelated_clear_test")
        connection.commit()
    assert memo_table is None
    assert unrelated_table is not None


def _create_test_user(cookies, username: str, role: str = "user") -> dict:
    created = client.post(
        "/api/auth/users",
        json={"username": username, "displayName": username.title(), "password": "pw123", "role": role},
        cookies=cookies,
    )
    if created.status_code == 409:
        users = client.get("/api/auth/users", cookies=cookies).json()["users"]
        return next(item for item in users if item["username"] == username)
    assert created.status_code == 200
    return created.json()["user"]


def test_regular_admin_respects_tool_visibility() -> None:
    """Regular admins should be subject to tool visibility settings on the homepage."""
    _reset_tool_access("text_cleaner")
    super_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    super_cookies = super_auth.cookies

    # Create a regular admin
    sub_admin = _create_test_user(super_cookies, "vis_admin_test", role="admin")
    sub_auth = client.post("/api/auth/login", json={"username": "vis_admin_test", "password": "pw123"})
    sub_cookies = sub_auth.cookies

    # Hide text_cleaner from everyone
    client.post(
        "/api/tools-admin/text_cleaner/access",
        json={"globalPublic": False, "allowedUserIds": []},
        cookies=super_cookies,
    )

    # Super admin can still see it
    super_tools = client.get("/api/tools", cookies=super_cookies).json()
    assert any(t["id"] == "text_cleaner" for t in super_tools)

    # Regular admin cannot see it on the homepage
    sub_tools = client.get("/api/tools", cookies=sub_cookies).json()
    assert all(t["id"] != "text_cleaner" for t in sub_tools)

    # Regular admin cannot access the tool directly
    detail = client.get("/api/tools/text_cleaner", cookies=sub_cookies)
    assert detail.status_code == 403
    assert detail.json()["error"]["code"] == "TOOL_ACCESS_DENIED"

    # But regular admin can still manage it in settings (tool list is unfiltered)
    access_list = client.get("/api/tools-admin/access", cookies=sub_cookies)
    assert access_list.status_code == 200
    assert any(item["tool"]["id"] == "text_cleaner" for item in access_list.json()["items"])

    # Grant access to the regular admin → now visible
    client.post(
        "/api/tools-admin/text_cleaner/access",
        json={"globalPublic": False, "allowedUserIds": [sub_admin["id"]]},
        cookies=super_cookies,
    )
    sub_tools_after = client.get("/api/tools", cookies=sub_cookies).json()
    assert any(t["id"] == "text_cleaner" for t in sub_tools_after)

    # Cleanup
    client.post(
        "/api/tools-admin/text_cleaner/access",
        json={"globalPublic": True, "allowedUserIds": []},
        cookies=super_cookies,
    )
    client.delete(f"/api/auth/users/{sub_admin['id']}", cookies=super_cookies)
    _reset_tool_access("text_cleaner")


def test_storage_usage_reports_user_and_tool_sizes() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    storage_dir = get_settings().storage_dir
    user_dir = storage_dir / "user_data" / "usage_test_user" / "tools" / "memo_demo"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "data.bin").write_bytes(b"x" * 200)

    response = client.get("/api/tools-admin/storage-usage", cookies=cookies)
    assert response.status_code == 200
    payload = response.json()
    assert payload["grandTotal"] >= 200
    tool_entry = next(t for t in payload["tools"] if t["toolId"] == "memo_demo")
    assert tool_entry["userBytes"] >= 200
    assert any(m["userId"] == "usage_test_user" and m["toolId"] == "memo_demo" for m in payload["matrix"])

    shutil.rmtree(user_dir.parent.parent, ignore_errors=True)


def test_admin_can_clear_single_user_tool_storage() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    storage_dir = get_settings().storage_dir
    user_dir = storage_dir / "user_data" / "clear_ut_user" / "tools" / "memo_demo"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "file.txt").write_text("hello", encoding="utf-8")

    cleared = client.delete("/api/tools-admin/memo_demo/users/clear_ut_user/storage", cookies=cookies)
    assert cleared.status_code == 200
    assert not user_dir.exists()


def test_admin_can_clear_all_user_storage() -> None:
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    storage_dir = get_settings().storage_dir
    user_tools = storage_dir / "user_data" / "clear_all_user" / "tools"
    dir_a = user_tools / "memo_demo"
    dir_b = user_tools / "text_cleaner"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    (dir_a / "a.txt").write_text("a", encoding="utf-8")
    (dir_b / "b.txt").write_text("b", encoding="utf-8")

    cleared = client.delete("/api/tools-admin/users/clear_all_user/storage", cookies=cookies)
    assert cleared.status_code == 200
    assert not dir_a.exists()
    assert not dir_b.exists()


def test_user_can_view_and_clear_own_storage() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    user = _create_test_user(admin_auth.cookies, "self_storage_user")
    user_auth = client.post("/api/auth/login", json={"username": "self_storage_user", "password": "pw123"})
    user_cookies = user_auth.cookies

    storage_dir = get_settings().storage_dir
    user_dir = storage_dir / "user_data" / user["id"] / "tools" / "memo_demo"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "note.txt").write_text("my note", encoding="utf-8")

    usage = client.get("/api/tools/my-storage", cookies=user_cookies)
    assert usage.status_code == 200
    assert usage.json()["totalBytes"] >= 7

    cleared = client.delete("/api/tools/my-storage/memo_demo", cookies=user_cookies)
    assert cleared.status_code == 200
    assert not user_dir.exists()

    client.delete(f"/api/auth/users/{user['id']}", cookies=admin_auth.cookies)


def test_super_admin_can_promote_and_demote() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = admin_auth.cookies
    user = _create_test_user(cookies, "role_test_user")

    promoted = client.post(f"/api/auth/users/{user['id']}/role", json={"role": "admin"}, cookies=cookies)
    assert promoted.status_code == 200
    assert promoted.json()["user"]["role"] == "admin"

    demoted = client.post(f"/api/auth/users/{user['id']}/role", json={"role": "user"}, cookies=cookies)
    assert demoted.status_code == 200
    assert demoted.json()["user"]["role"] == "user"

    client.delete(f"/api/auth/users/{user['id']}", cookies=cookies)


def test_regular_admin_cannot_create_admin() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    super_cookies = admin_auth.cookies
    sub_admin = _create_test_user(super_cookies, "sub_admin_test", role="admin")
    sub_auth = client.post("/api/auth/login", json={"username": "sub_admin_test", "password": "pw123"})
    sub_cookies = sub_auth.cookies

    attempt = client.post(
        "/api/auth/users",
        json={"username": "should_fail_admin", "displayName": "Fail", "password": "pw", "role": "admin"},
        cookies=sub_cookies,
    )
    assert attempt.status_code == 403
    assert attempt.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    client.delete(f"/api/auth/users/{sub_admin['id']}", cookies=super_cookies)


def test_regular_admin_cannot_delete_admin() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    super_cookies = admin_auth.cookies
    sub_admin = _create_test_user(super_cookies, "del_admin_test", role="admin")
    sub_auth = client.post("/api/auth/login", json={"username": "del_admin_test", "password": "pw123"})
    sub_cookies = sub_auth.cookies

    attempt = client.delete(f"/api/auth/users/{sub_admin['id']}", cookies=sub_cookies)
    assert attempt.status_code == 403

    client.delete(f"/api/auth/users/{sub_admin['id']}", cookies=super_cookies)


def test_super_admin_can_delete_admin() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    super_cookies = admin_auth.cookies
    sub_admin = _create_test_user(super_cookies, "del_admin_test2", role="admin")

    deleted = client.delete(f"/api/auth/users/{sub_admin['id']}", cookies=super_cookies)
    assert deleted.status_code == 200


def test_cannot_delete_super_admin() -> None:
    admin_auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = admin_auth.cookies
    admin_user = next(u for u in client.get("/api/auth/users", cookies=cookies).json()["users"] if u["username"] == "admin")

    attempt = client.delete(f"/api/auth/users/{admin_user['id']}", cookies=cookies)
    assert attempt.status_code == 400
    assert attempt.json()["error"]["code"] == "CANNOT_DELETE_SUPER_ADMIN"


def test_experiment_monitor_create_task_uses_preview_processes() -> None:
    _cleanup_experiment_test_data()
    experiment_service.init_database(EXPERIMENT_USER.id)
    server_id = "experiment_preview_server"
    with user_tool_connection_context(EXPERIMENT_USER.id, "experiment_monitor") as connection:
        connection.execute(
            """
            INSERT INTO em_servers (
                id, name, host, port, ssh_username, ssh_password_encrypted,
                owner_user_id, enabled, created_at, updated_at
            ) VALUES (?, 'Preview Server', '127.0.0.1', 22, 'tester', ?, ?, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (server_id, experiment_service.encrypt_secret("secret"), EXPERIMENT_USER.id),
        )
        connection.commit()

    preview_processes = [f"tester {idx} 1 S 00:01 python train.py --rank {idx}" for idx in range(10)]
    task = experiment_service.create_monitor_task(
        {
            "serverId": server_id,
            "name": "Preview Count",
            "matchMode": "simple",
            "matchPattern": "python train.py",
            "filterUser": "tester",
            "alertCondition": "below",
            "alertThreshold": 5,
            "initialMatchedProcesses": preview_processes,
        },
        EXPERIMENT_USER,
    )

    history = experiment_service.get_task_history(task["id"], EXPERIMENT_USER)
    assert history["samples"][-1]["processCount"] == 10
    # get_task_history uses lightweight queries (no matchedProcesses),
    # so verify matchedProcesses via direct DB query
    with user_tool_connection_context(EXPERIMENT_USER.id, "experiment_monitor") as connection:
        sample_row = connection.execute(
            "SELECT matched_processes FROM em_samples WHERE task_id = ? ORDER BY checked_at DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
    assert experiment_service.json.loads(sample_row["matched_processes"]) == preview_processes
    assert history["alertState"]["lastCheckCount"] == 10
    with user_tool_connection_context(EXPERIMENT_USER.id, "experiment_monitor") as connection:
        task_row = connection.execute(
            "SELECT id, last_checked_at, check_interval_seconds FROM em_monitor_tasks WHERE id = ?",
            (task["id"],),
        ).fetchone()
    assert experiment_service._task_check_due(task_row, experiment_service.datetime.now(experiment_service.timezone.utc)) is False
    updated_processes = preview_processes[:2]
    experiment_service.update_monitor_task(
        task["id"],
        {
            "matchPattern": "SCREEN -dmS op_",
            "initialMatchedProcesses": updated_processes,
        },
        EXPERIMENT_USER,
    )
    edited_history = experiment_service.get_task_history(task["id"], EXPERIMENT_USER)
    assert edited_history["samples"][-1]["processCount"] == 2
    # Verify updated matchedProcesses via direct DB query
    with user_tool_connection_context(EXPERIMENT_USER.id, "experiment_monitor") as connection:
        updated_sample_row = connection.execute(
            "SELECT matched_processes FROM em_samples WHERE task_id = ? ORDER BY checked_at DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
    assert experiment_service.json.loads(updated_sample_row["matched_processes"]) == updated_processes
    assert edited_history["alertState"]["lastCheckCount"] == 2
    _cleanup_experiment_test_data()


def test_memo_demo_requires_login() -> None:
    client.cookies.clear()
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


def test_server_monitor_parse_output() -> None:
    parsed = monitor_service.parse_monitor_output(_monitor_output())
    assert parsed["cpuPercent"] is not None
    assert parsed["memoryTotalBytes"] == 16_000_000 * 1024
    assert parsed["memoryUsedBytes"] == 4_000_000 * 1024
    assert parsed["disks"][0]["mountPath"] == "/"
    assert parsed["gpus"][1]["index"] == 1


def test_server_monitor_parse_gpu_processes() -> None:
    parsed = monitor_service.parse_monitor_output(_monitor_output_with_processes())
    gpu = parsed["gpus"][0]
    assert gpu["processCount"] == 1
    assert gpu["processes"][0]["pid"] == 1234
    assert gpu["processes"][0]["usedMemoryMiB"] == 2048
    assert gpu["processes"][0]["cpuPercent"] == 12.5
    assert gpu["processes"][0]["gpuPercent"] == 80
    assert gpu["processes"][0]["username"] == "alice"
    assert gpu["processes"][0]["command"] == "python train.py --epochs 10"


def test_server_monitor_default_visibility_and_snapshot(monkeypatch) -> None:
    _cleanup_monitor_test_data()
    monkeypatch.setattr(monitor_service, "_run_ssh", lambda row, command, timeout=20: _monitor_output())
    monkeypatch.setattr(monitor_service, "_run_ssh_cached", lambda row, command: _monitor_output())
    auth = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    cookies = auth.cookies
    created = client.post(
        "/api/tools/server-monitor/servers",
        json={
            "name": "Default Monitor Test",
            "host": "127.0.0.1",
            "port": 22,
            "sshUsername": "root",
            "sshPassword": "secret",
            "isDefault": True,
            "directoryWhitelist": ["/data"],
            "directoryRefreshSeconds": 60,
        },
        cookies=cookies,
    )
    assert created.status_code == 200
    server = created.json()["server"]
    assert server["isDefault"] is True

    # Default servers are visible to any logged-in user (data is per-user now)
    admin_list = client.get("/api/tools/server-monitor/servers", cookies=cookies)
    assert admin_list.status_code == 200
    assert any(item["id"] == server["id"] for item in admin_list.json()["servers"])

    snapshot = client.get(f"/api/tools/server-monitor/servers/{server['id']}/snapshot?force=true", cookies=cookies)
    assert snapshot.status_code == 200
    assert snapshot.json()["sample"]["gpus"][0]["name"] == "NVIDIA A100"
    _cleanup_monitor_test_data()


def test_server_monitor_private_visibility_and_directory_whitelist(monkeypatch) -> None:
    _cleanup_monitor_test_data()
    monkeypatch.setattr(
        monitor_service,
        "_run_ssh",
        lambda row, command, timeout=20: "dev 1000 400 600 40% /data\n123\t/data/project\n",
    )
    admin = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    username = "monitor_private_user"
    created_user = client.post(
        "/api/auth/users",
        json={"username": username, "displayName": "Private User", "password": "pw123", "role": "user"},
        cookies=admin.cookies,
    )
    if created_user.status_code == 409:
        client.post(f"/api/auth/users/{next(item for item in client.get('/api/auth/users', cookies=admin.cookies).json()['users'] if item['username'] == username)['id']}/disabled", json={"disabled": False}, cookies=admin.cookies)
    user_auth = client.post("/api/auth/login", json={"username": username, "password": "pw123"})
    assert user_auth.status_code == 200

    private_server = client.post(
        "/api/tools/server-monitor/servers",
        json={
            "name": "Private Monitor Test",
            "host": "10.0.0.8",
            "port": 22,
            "sshUsername": "ops",
            "sshPassword": "secret",
            "isDefault": False,
            "directoryWhitelist": ["/data"],
            "directoryRefreshSeconds": 60,
        },
        cookies=user_auth.cookies,
    )
    assert private_server.status_code == 200
    server = private_server.json()["server"]

    client.cookies.clear()
    anonymous_list = client.get("/api/tools/server-monitor/servers")
    assert anonymous_list.status_code == 200
    assert all(item["id"] != server["id"] for item in anonymous_list.json()["servers"])

    allowed = client.post(
        f"/api/tools/server-monitor/servers/{server['id']}/directories",
        json={"path": "/data/project"},
        cookies=user_auth.cookies,
    )
    assert allowed.status_code == 200
    assert allowed.json()["usedBytes"] == 123

    listing = client.get(f"/api/tools/server-monitor/servers/{server['id']}/directories", cookies=user_auth.cookies)
    assert listing.status_code == 200
    assert listing.json()["directories"][0]["path"] == "/data/project"

    denied = client.post(
        f"/api/tools/server-monitor/servers/{server['id']}/directories",
        json={"path": "/etc"},
        cookies=user_auth.cookies,
    )
    assert denied.status_code == 403
    _cleanup_monitor_test_data()


def _monitor_output() -> str:
    return """__CPU__
cpu  100 0 100 800 0 0 0 0 0 0
cpu  120 0 120 860 0 0 0 0 0 0
__MEMINFO__
MemTotal:       16000000 kB
MemAvailable:  12000000 kB
__DF__
Filesystem 1B-blocks Used Available Use% Mounted on
/dev/root 1000000000 400000000 600000000 40% /
__GPU__
0, NVIDIA A100, 55, 40960, 20480, 61, 190
1, NVIDIA A100, 20, 40960, 1024, 45, 120
"""


def _cleanup_monitor_test_data() -> None:
    # Clean up platform-level default servers table
    with get_connection() as connection:
        # Check if monitor_default_servers table exists before trying to delete
        table_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='monitor_default_servers'"
        ).fetchone()
        if table_exists:
            connection.execute(
                "DELETE FROM monitor_default_servers WHERE name IN ('Default Monitor Test', 'Private Monitor Test')"
            )
        connection.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username IN ('monitor_user_test', 'monitor_private_user'))"
        )
        connection.execute("DELETE FROM users WHERE username IN ('monitor_user_test', 'monitor_private_user')")
        connection.commit()

    # Clean up per-user databases for server_monitor
    for user_id, _db_path in list_user_tool_dbs("server_monitor"):
        try:
            with user_tool_connection_context(user_id, "server_monitor") as connection:
                server_ids = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM monitor_servers WHERE name IN ('Default Monitor Test', 'Private Monitor Test')"
                    ).fetchall()
                ]
                for server_id in server_ids:
                    connection.execute("DELETE FROM monitor_samples WHERE server_id = ?", (server_id,))
                    connection.execute("DELETE FROM monitor_directory_cache WHERE server_id = ?", (server_id,))
                    connection.execute("DELETE FROM monitor_servers WHERE id = ?", (server_id,))
                connection.commit()
        except Exception:
            pass  # Ignore cleanup errors for per-user DBs


def _cleanup_experiment_test_data() -> None:
    try:
        with user_tool_connection_context(EXPERIMENT_USER.id, "experiment_monitor") as connection:
            task_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM em_monitor_tasks WHERE owner_user_id = ?",
                    (EXPERIMENT_USER.id,),
                ).fetchall()
            ]
            for task_id in task_ids:
                connection.execute("DELETE FROM em_alert_actions WHERE task_id = ?", (task_id,))
                connection.execute("DELETE FROM em_alert_events WHERE task_id = ?", (task_id,))
                connection.execute("DELETE FROM em_alert_states WHERE task_id = ?", (task_id,))
                connection.execute("DELETE FROM em_samples WHERE task_id = ?", (task_id,))
                connection.execute("DELETE FROM em_monitor_tasks WHERE id = ?", (task_id,))
            connection.execute("DELETE FROM em_servers WHERE owner_user_id = ?", (EXPERIMENT_USER.id,))
            connection.commit()
    except Exception:
        pass  # Ignore cleanup errors for per-user DBs


def _reset_tool_access(tool_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM platform_tool_visibility WHERE tool_id = ?", (tool_id,))
        connection.execute("DELETE FROM platform_tool_user_access WHERE tool_id = ?", (tool_id,))
        connection.commit()


def _monitor_output_with_processes() -> str:
    return """__CPU__
cpu  100 0 100 800 0 0 0 0 0 0
cpu  120 0 120 860 0 0 0 0 0 0
__MEMINFO__
MemTotal:       16000000 kB
MemAvailable:  12000000 kB
__DF__
Filesystem 1B-blocks Used Available Use% Mounted on
/dev/root 1000000000 400000000 600000000 40% /
__GPU__
0, GPU-abc, NVIDIA A100, 70, 40960, 10240, 55, 180
__GPU_PROCESSES__
GPU-abc, 1234, python, 2048
__PROC_STATS__
1234 alice 12.5 4.2 python python train.py --epochs 10
__GPU_PMON__
0 1234 C 80 20 - - python
"""
