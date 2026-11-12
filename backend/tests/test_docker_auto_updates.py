from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError, toolbox_error_handler
from backend.app.db.database import get_connection, init_database
from backend.app.services import ssh_server_service
from backend.app.services.auth_service import User
from tools.docker_manager.backend import auto_updates as updates, base, update_remote as remote
from tools.docker_manager.backend import router as docker_router
from tools.docker_manager.backend.update_spec import UpdateRuleInput, parse_run, rule_target

OLD = "a" * 64
NEW = "b" * 64
OLD_IMAGE = "sha256:" + "c" * 64
NEW_IMAGE = "sha256:" + "d" * 64
MANIFEST = "sha256:" + "e" * 64
RECIPE = "docker run -d --name app -v app-storage:/app/storage -e SESSION_SECRET=secret-value app:stable"
RULE = UpdateRuleInput(mode="run", recipe=RECIPE, recipients=["team@example.com"])


@pytest.fixture
def server(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setenv("STORAGE_DIR", str(tmp_path))
        patch.setenv("PLATFORM_DB_PATH", str(tmp_path / "platform.db"))
        patch.setattr(base, "_DB_INITIALIZED", False)
        patch.setattr(base, "_start_df_cache_refresher", lambda: None)
        get_settings.cache_clear()
        init_database()
        base.init_docker_database()
        owner = User("owner", "owner", "Owner")
        admin = User("manager", "manager", "Admin", "admin")
        viewer = User("viewer", "viewer", "Viewer")
        with get_connection() as conn:
            conn.executemany("""
                INSERT INTO users (id, username, display_name, password_hash, password_salt, role, disabled, created_at)
                VALUES (?, ?, ?, 'hash', 'salt', ?, 0, ?)
            """, [(user.id, user.username, user.display_name, user.role, base._now()) for user in (owner, admin, viewer)])
        host = ssh_server_service.create_server({
            "name": "Docker test", "host": "192.0.2.10", "sshUsername": "operator",
            "sshPassword": "fixture-password", "isPublic": True, "allowedUserIds": [viewer.id],
        }, admin)
        server_id = host["id"]
        with get_connection() as conn:
            conn.execute("""
                UPDATE platform_ssh_servers SET owner_user_id=? WHERE id=?
            """, (owner.id, server_id))
            conn.execute("""
                INSERT INTO docker_servers (id, name, host, port, ssh_username, ssh_password_encrypted,
                    created_by, created_at, updated_at) VALUES (?, ?, ?, 22, ?, '', ?, ?, ?)
            """, (server_id, host["name"], host["host"], host["sshUsername"], admin.id, base._now(), base._now()))
            for user in (owner, viewer):
                conn.execute("""
                    INSERT INTO docker_user_perms (server_id, user_id, server_visible, updated_at)
                    VALUES (?, ?, 1, ?)
                """, (server_id, user.id, base._now()))
        yield server_id, owner, admin, viewer
    get_settings.cache_clear()


def test_server_permission_and_revoke_queued_rule(server):
    server_id, owner, admin, viewer = server
    rule = updates.save_rule(server_id, owner, RULE.model_copy(update={"enabled": True}))
    assert not rule["enabled"]
    assert updates.list_rules(server_id, admin)[0]["id"] == rule["id"]
    with pytest.raises(ToolboxError) as denied:
        updates.list_rules(server_id, viewer)
    assert denied.value.status_code == 403
    with updates.db(owner.id) as conn:
        encrypted = conn.execute("SELECT config_encrypted FROM docker_update_rules").fetchone()[0]
    assert "secret-value" not in encrypted
    assert updates.get_rule(server_id, admin, rule["id"])["recipe"] == RECIPE
    updates.request_run(server_id, owner, rule["id"])
    with get_connection() as conn:
        conn.execute("UPDATE docker_user_perms SET server_visible=0 WHERE user_id=?", (owner.id,))
    updates.process_updates()
    assert updates.list_rules(server_id, admin)[0]["pauseReason"]
    assert updates.list_runs(server_id, admin, rule["id"])[0]["stage"] == "permission_revoked"


def test_concurrent_triggers_claim_one_execution(server):
    server_id, owner, admin, _ = server
    rule = updates.save_rule(server_id, owner, RULE)

    def trigger(user):
        try:
            return updates.request_run(server_id, user, rule["id"])
        except ToolboxError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(trigger, (owner, admin)))
    assert results.count("UPDATE_IN_PROGRESS") == 1
    assert len(updates.list_runs(server_id, owner, rule["id"])) == 1
    with pytest.raises(ToolboxError, match="已有自动更新规则"):
        updates.save_rule(server_id, admin, RULE)


def test_auto_update_api_enforces_permissions_and_queues_work(server, monkeypatch):
    server_id, owner, _, viewer = server
    app = FastAPI()
    app.add_exception_handler(ToolboxError, toolbox_error_handler)
    app.include_router(docker_router.router, prefix="/api/tools/docker-manager")
    monkeypatch.setattr(docker_router, "require_user", lambda request: owner if request.headers.get("x-owner") else viewer)
    client = TestClient(app)
    path = f"/api/tools/docker-manager/servers/{server_id}/auto-updates"
    headers = {"x-owner": "1"}
    assert client.post(path, json=RULE.model_dump()).status_code == 403
    response = client.post(path, json=RULE.model_dump(), headers=headers)
    assert response.status_code == 201
    rule = response.json()["rule"]
    assert "recipe" not in rule
    path = f"{path}/{rule['id']}"
    assert client.get(path).status_code == 403
    assert client.get(f"{path}/runs").status_code == 403
    assert client.get(path, headers=headers).json()["rule"]["recipe"] == RECIPE
    response = client.post(f"{path}/check", headers=headers)
    assert response.status_code == 202
    [run] = client.get(f"{path}/runs", headers=headers).json()["runs"]
    assert run["id"] == response.json()["runId"]
    assert run["status"] == "queued"


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def snapshot():
    return remote.Snapshot([{"Id": OLD, "Name": "/app"}], "linux/amd64", OLD_IMAGE,
                           False, parse_run(RECIPE), [], "test-engine")


def test_unchanged_and_handoff_recovery(server, monkeypatch):
    server_id, owner, _, _ = server
    rule = updates.save_rule(server_id, owner, RULE)
    monkeypatch.setattr(base, "_ssh_connect", lambda _: Connection())
    monkeypatch.setattr(base, "_refresh_docker_df_cache_best_effort", lambda _: None)
    monkeypatch.setattr(remote, "preflight", lambda *_: "test-engine")
    monkeypatch.setattr(remote, "work_dir", lambda _, run_id: f"/spool/{run_id}")
    monkeypatch.setattr(remote, "snapshot", lambda *_: snapshot())
    monkeypatch.setattr(remote, "clean", lambda *_: None)
    monkeypatch.setattr(updates, "_verify_detached_runner", lambda *_: None)
    monkeypatch.setattr(remote, "stage_worker", lambda *_: None)
    monkeypatch.setattr(remote, "candidate_image", lambda *_: remote.Candidate("app@" + MANIFEST, MANIFEST, OLD_IMAGE))
    updates.request_run(server_id, owner, rule["id"])
    updates.process_updates()
    assert updates.list_runs(server_id, owner, rule["id"])[0]["status"] == "no_change"

    mails = []
    monkeypatch.setattr(updates.email_service, "send_email", lambda *args: mails.append(args) or True)
    monkeypatch.setattr(remote, "candidate_image", lambda *_: remote.Candidate("app@" + MANIFEST, MANIFEST, NEW_IMAGE))

    def interrupted_handoff(*_):
        assert len(mails) == 1
        assert updates.list_runs(server_id, owner, rule["id"])[0]["status"] == "handoff"
        raise remote.RemoteUpdateError("handoff")

    monkeypatch.setattr(remote, "launch", interrupted_handoff)
    with get_connection() as conn:
        base._record_resource_creator(conn, server_id, "container", OLD[:12], owner.id)
        conn.execute("UPDATE docker_containers_meta SET is_public=1 WHERE container_ref=?", (OLD[:12],))
    run_id = updates.request_run(server_id, owner, rule["id"])
    updates.process_updates()
    assert len(mails) == 1
    monkeypatch.setattr(remote, "outcome", lambda *_: ("succeeded", "verified", {OLD: NEW}))
    updates.process_updates()
    updates.process_updates()
    run = updates.list_runs(server_id, owner, rule["id"])[0]
    assert (run["id"], run["status"], run["startMail"], run["finishMail"]) == (run_id, "succeeded", "sent", "sent")
    assert len(mails) == 2 and all(mail[0] == RULE.recipients for mail in mails)
    with get_connection() as conn:
        assert conn.execute("SELECT is_public FROM docker_containers_meta WHERE container_ref=?", (NEW[:12],)).fetchone()[0]
        assert not conn.execute("SELECT 1 FROM docker_resource_roles WHERE resource_ref=?", (OLD[:12],)).fetchone()
        assert conn.execute("SELECT 1 FROM docker_resource_roles WHERE resource_ref=?", (NEW[:12],)).fetchone()


def test_screen_probe_closes_ssh_transport_before_requesting_result(monkeypatch):
    events = []

    class ProbeLease(Connection):
        def invalidate(self):
            events.append("disconnected")

    class ProbePool:
        def borrow_client(self, spec):
            assert spec == "ssh-config"
            return ProbeLease()

    monkeypatch.setattr(base, "_ssh_spec", lambda _: "ssh-config")
    monkeypatch.setattr(updates, "SSHConnectionPool", ProbePool)
    monkeypatch.setattr(remote, "work_dir", lambda _, probe_id: f"/spool/{probe_id}")
    monkeypatch.setattr(remote, "start_detach_probe", lambda *_: events.append("started"))
    monkeypatch.setattr(remote, "verify_detach_probe", lambda *_: events.append("verified"))
    monkeypatch.setattr(remote, "clean", lambda *_: events.append("cleaned"))
    updates._verify_detached_runner(None, Connection(), "docker-id")
    assert events == ["started", "disconnected", "verified", "cleaned"]


@pytest.mark.skipif(shutil.which("screen") is None, reason="GNU Screen is unavailable")
def test_screen_launch_survives_launcher_exit_and_reports_result(tmp_path, monkeypatch):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, content in {
        "docker": "#!/bin/bash\nprintf 'docker-id\\n'\n",
        "timeout": '#!/bin/bash\nshift 3\nexec "$@"\n',
        "flock": "#!/bin/bash\nexit 0\n",
    }.items():
        executable = binaries / name
        executable.write_text(content)
        executable.chmod(0o700)
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"}

    def local_execute(_client, args, stage, timeout=30):
        completed = subprocess.run(args, env=env, text=True, capture_output=True, timeout=timeout)
        if completed.returncode:
            raise remote.RemoteUpdateError(stage)
        return completed.stdout.strip()

    monkeypatch.setattr(remote, "execute", local_execute)
    run_id = uuid.uuid4().hex[:12]
    probe_id = f"probe-{run_id}"
    probe = str(tmp_path / probe_id)
    work = tmp_path / "work"
    work.mkdir()
    (work / "worker.sh").write_text("""\
for ((i=0; i<50; i++)); do
    if [ -f "$1/release" ]; then
        echo 'failed pull_failed' > "$1/result.tmp"
        mv "$1/result.tmp" "$1/result"
        exit
    fi
    sleep 0.1
done
""")
    try:
        assert remote.preflight(None, compose=True) == "docker-id"
        remote.start_detach_probe(None, probe_id, probe)
        assert not (tmp_path / probe_id / "engine").exists()
        remote.verify_detach_probe(None, probe, "docker-id")
        remote.launch(None, run_id, str(work))
        assert remote.outcome(None, run_id, str(work)) is None
        (work / "release").touch()
        for _ in range(30):
            result = remote.outcome(None, run_id, str(work))
            if result is not None:
                break
            time.sleep(0.1)
        assert result == ("failed", "pull_failed", {})
    finally:
        for session in (f"toolbox-{probe_id}", f"toolbox-update-{run_id}"):
            subprocess.run(["screen", "-S", session, "-X", "quit"], env=env, capture_output=True, timeout=5)


def test_manifest_uses_target_platform_digest(monkeypatch):
    amd_digest = "sha256:" + "1" * 64
    arm_digest = "sha256:" + "2" * 64

    def query(_, args, stage):
        assert stage == "registry_query"
        if "--format" in args:
            return {"manifests": [
                {"digest": amd_digest, "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": arm_digest, "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
                {"digest": MANIFEST, "platform": {"os": "unknown", "architecture": "unknown"}},
            ]}
        assert args[-1] == f"registry.example:5000/app@{arm_digest}"
        return {"config": {"digest": NEW_IMAGE}}

    monkeypatch.setattr(remote, "remote_json", query)
    assert remote.candidate_image(None, "registry.example:5000/app:stable", "linux/arm64").image == NEW_IMAGE


def test_self_update_validates_storage_ports_and_session_secret():
    item = {
        "Config": {"Env": ["SESSION_SECRET=old-secret"]},
        "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8080"}]}},
        "Mounts": [{"Destination": "/app/storage", "Name": "toolbox-storage", "RW": True}],
    }
    remote._verify_self_update(item, {"SESSION_SECRET": "old-secret"}, [("8000/tcp", "0.0.0.0", "8080")])
    with pytest.raises(ToolboxError, match="SESSION_SECRET"):
        remote._verify_self_update(item, {"SESSION_SECRET": "changed"}, [("8000/tcp", "", "8080")])


def test_recipe_is_digest_pinned_and_compose_must_be_standalone():
    recipe = parse_run("docker run -dit --name=app --env 'TEXT=a; $b' nginx:stable sleep 10")
    assert recipe.command("nginx@" + MANIFEST, [])[0:4] == ["docker", "run", "--detach", "--pull=never"]
    assert "--env=TEXT=a; $b" in recipe.command("nginx@" + MANIFEST, [])
    assert recipe.command("nginx@" + MANIFEST, [])[-4:] == ["--", "nginx@" + MANIFEST, "sleep", "10"]
    with pytest.raises(ToolboxError, match="外部配置"):
        rule_target(UpdateRuleInput(mode="compose", project="project", service="web",
                                    recipe="services: {web: {image: nginx:stable, env_file: .env}}"))


FAKE_DOCKER = r"""
import json
import sys
from pathlib import Path

state_file = Path("docker-state.json")
state = json.loads(state_file.read_text())
args = sys.argv[1:]
state["calls"].append(args[0])
state_file.write_text(json.dumps(state))
old, new = state["old"], state["new"]
containers = state["containers"]

def save():
    state_file.write_text(json.dumps(state))

def get(ref):
    for ident, item in containers.items():
        if ref in (ident, item["name"]):
            return ident, item
    sys.exit(1)

if args[0] == "info":
    print("test-engine")
elif args[0] == "pull":
    sys.exit(int(state["scenario"] == "pull_failed"))
elif args[:2] == ["image", "inspect"]:
    print(state["new_image"] if args[3] == "{{.Id}}" else "linux/amd64")
elif args[:2] == ["container", "inspect"]:
    ident, item = get(args[-1])
    fmt = args[3]
    if fmt == "{{.Id}}":
        print(ident)
    elif fmt == "{{.Name}}":
        print("/" + item["name"])
    elif fmt.startswith("{{range .Mounts}}"):
        print("volume app-storage /app/storage")
    elif fmt == "{{.RestartCount}}":
        print("0")
    elif fmt.startswith("{{index"):
        print(item.get("label", "False"))
    else:
        suffix = f" {item['health']} 0" if "State.Health" in fmt else ""
        print(f"{item['image']} {item['status']}{suffix}")
elif args[0] == "ps":
    print("\n".join(containers))
elif args[0] == "image" and args[1] in ("tag", "rm"):
    pass
elif args[0] == "compose":
    rollback = "previous.json" in args
    item = containers.pop(next(iter(containers)))
    item["image"] = state["old_image"] if rollback else state["new_image"]
    item["health"] = "unhealthy" if not rollback and state["scenario"] == "unhealthy" else "healthy"
    containers[("f" * 64) if rollback else new] = item
    save()
elif args[0] in {"stop", "start", "rename", "rm"}:
    ident, item = get(args[-2] if args[0] == "rename" else args[-1])
    if args[0] == "rm":
        del containers[ident]
    elif args[0] == "rename":
        item["name"] = args[-1]
    else:
        item["status"] = "running" if args[0] == "start" else "exited"
    save()
elif args[0] == "run":
    containers[new] = {"name": "app", "image": state["new_image"], "status": "running",
                       "health": "unhealthy" if state["scenario"] == "unhealthy" else "healthy",
                       "label": "test-run"}
    save()
elif args[0] == "exec":
    pass
else:
    sys.exit(1)
"""


@pytest.mark.parametrize(("mode", "scenario", "expected"), [
    ("run", "healthy", "succeeded verified"),
    ("run", "unhealthy", "rolled_back unhealthy"),
    ("run", "pull_failed", "failed pull_failed"),
    ("compose", "healthy", "succeeded verified"),
    ("compose", "unhealthy", "rolled_back unhealthy"),
])
def test_host_worker_finishes_and_rolls_back(tmp_path, monkeypatch, mode, scenario, expected):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, script in {
        "docker": f"#!{sys.executable}\n{FAKE_DOCKER}",
        "timeout": '#!/bin/bash\nshift 3\nexec "$@"\n',
        "flock": "#!/bin/bash\nexit 0\n",
    }.items():
        path = binaries / name
        path.write_text(script)
        path.chmod(0o700)
    workspace = tmp_path / "test-run"
    workspace.mkdir()
    state = {"old": OLD, "new": NEW, "old_image": OLD_IMAGE, "new_image": NEW_IMAGE, "scenario": scenario,
             "calls": [], "containers": {OLD: {"name": "app", "image": OLD_IMAGE, "status": "running", "health": "healthy"}}}
    (workspace / "docker-state.json").write_text(json.dumps(state))

    def upload(_, path, files):
        for name, content in files.items():
            (workspace / name).write_text(content)

    monkeypatch.setattr(remote, "upload", upload)
    payload = RULE if mode == "run" else UpdateRuleInput(
        mode=mode, project="project", service="app", recipe="services: {app: {image: app:stable}}",
    )
    current = snapshot()
    if mode == "compose":
        current = remote.Snapshot(current.containers, current.platform, current.image, True, None, [], current.engine_id)
        (workspace / "compose.yaml").write_text(payload.recipe)
    remote.stage_worker(None, "test-run", str(workspace), payload, rule_target(payload)[0], current,
                        remote.Candidate("app@" + MANIFEST, MANIFEST, NEW_IMAGE))
    subprocess.run(["bash", str(workspace / "worker.sh"), str(workspace)], check=True, timeout=20,
                   env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"}, start_new_session=True)
    result = (workspace / "result").read_text().split()
    assert " ".join(result[:2]) == expected
    recorded = json.loads((workspace / "docker-state.json").read_text())
    if scenario == "pull_failed":
        assert "stop" not in recorded["calls"] and "rename" not in recorded["calls"]
    if scenario != "pull_failed":
        final_id = NEW if scenario == "healthy" else (OLD if mode == "run" else "f" * 64)
        assert result[2] == f"{OLD}={final_id}"
        assert list(recorded["containers"]) == [final_id]
        if mode == "compose":
            assert "exec" in recorded["calls"]
    assert not (workspace / "plan.sh").exists()
