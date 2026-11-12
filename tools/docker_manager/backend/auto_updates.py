"""User-scoped update rules, queued executions, and remote reconciliation."""
from __future__ import annotations

import fcntl
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection, list_user_tool_dbs, user_tool_connection_context
from backend.app.services import email_service, ssh_server_service, tool_access_service
from backend.app.services.auth_service import User, user_from_row
from backend.app.services.ssh_connection_service import SSHConnectionPool

from . import base, update_remote as remote
from .update_spec import UpdateRuleInput, rule_target

logger = logging.getLogger(__name__)
TOOL_ID = base.TOOL_ID
ACTIVE = ("queued", "checking", "handoff")
STAGES = {
    "preflight": "无法检查目标服务器的更新环境。",
    "screen_missing": "目标服务器缺少 GNU Screen，请为 SSH 用户安装或配置 screen。",
    "worker_tools_missing": "目标服务器缺少 flock 或 timeout 命令。",
    "docker_unavailable": "目标服务器的 SSH 用户无法直接访问 Docker。",
    "buildx_missing": "目标服务器的 SSH 用户无法运行 Docker Buildx。",
    "compose_missing": "目标服务器的 SSH 用户无法运行 Docker Compose。",
    "screen_detach": "SSH 断开后未收到 screen 测试结果，请检查宿主机是否在退出登录时清理后台进程。",
    "target_missing": "找不到目标容器或 Compose 服务。",
    "target_not_running": "目标容器或服务副本当前未全部运行。",
    "compose_config": "目标服务器无法解析 Compose 配置。",
    "registry_query": "查询镜像仓库失败，请检查目标服务器的网络和 Docker 登录。",
    "registry_platform": "仓库镜像缺少唯一匹配的目标平台。",
    "registry_digest": "仓库未返回可验证的镜像摘要。",
    "permission_revoked": "负责人已失去更新权限，规则已暂停。",
    "unchanged": "当前容器已运行该镜像。",
    "previous_failure": "该版本上次更新失败，可手动重新检查。",
    "target_changed": "目标容器在更新前发生变化。",
    "target_metadata": "无法读取目标容器或镜像信息。",
    "target_busy": "目标正在执行另一项更新。",
    "pull_failed": "拉取新镜像失败，原容器仍保留。",
    "image_mismatch": "拉取的镜像与预期摘要不符。",
    "docker_context": "远程任务使用的 Docker 环境发生变化。",
    "verified": "容器和健康检查通过。",
    "start_failed": "新容器启动失败，已回滚。",
    "stop_failed": "停止旧容器失败，已恢复旧容器。",
    "rename_failed": "切换旧容器失败，已恢复旧容器。",
    "unhealthy": "新容器未通过健康检查，已回滚。",
    "rollback_failed": "回滚失败，请在宿主机核对容器状态。",
    "rollback_health": "旧容器未通过回滚后的健康检查。",
    "cleanup_failed": "容器验收通过，旧容器或临时镜像清理失败。",
    "worker_missing": "宿主机上未找到运行结果，请核对容器和系统日志。",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS docker_update_rules (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    target TEXT NOT NULL,
    image TEXT NOT NULL,
    config_encrypted TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    next_at TEXT NOT NULL,
    failed_digest TEXT NOT NULL DEFAULT '',
    pause_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS docker_update_runs (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES docker_update_rules(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    manual INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    remote_path TEXT NOT NULL DEFAULT '',
    old_ids TEXT NOT NULL DEFAULT '{}',
    old_image TEXT NOT NULL DEFAULT '',
    new_image TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL DEFAULT '',
    recipients_encrypted TEXT NOT NULL DEFAULT '',
    start_mail TEXT NOT NULL DEFAULT 'skipped',
    finish_mail TEXT NOT NULL DEFAULT 'skipped'
);
CREATE UNIQUE INDEX IF NOT EXISTS docker_update_active_run ON docker_update_runs(rule_id)
WHERE status IN ('queued', 'checking', 'handoff');
CREATE INDEX IF NOT EXISTS docker_update_run_history ON docker_update_runs(rule_id, created_at);
"""


@contextmanager
def db(user_id: str) -> Iterator[sqlite3.Connection]:
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.executescript(SCHEMA)
        yield conn


@contextmanager
def lock(name: str, *, nonblocking: bool = False) -> Iterator[bool]:
    directory = get_settings().storage_dir / "data" / "tools" / TOOL_ID
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def error(code: str, message: str, status: int) -> ToolboxError:
    return ToolboxError(code, message, status_code=status, tool_id=TOOL_ID)


def require_server(server_id: str, user: User) -> sqlite3.Row:
    tool_access_service.require_tool_access(TOOL_ID, user)
    if user.disabled:
        raise error("PERMISSION_DENIED", "账号已停用", 403)
    base.init_docker_database()
    with get_connection() as conn:
        server = base._get_server_row(conn, server_id)
        ssh_server = ssh_server_service.get_server(server_id, user)
        perms = base._get_user_perms(conn, server_id, user)
    if not base._can_manage_auto_updates(ssh_server, perms):
        raise error("AUTO_UPDATE_PERMISSION_DENIED", "您没有在此服务器管理自动更新的权限。", 403)
    return server


def _get_user(user_id: str) -> User:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise error("PERMISSION_DENIED", "规则负责人不存在", 403)
    return user_from_row(row)


def _config(row: sqlite3.Row) -> UpdateRuleInput:
    return UpdateRuleInput.model_validate_json(base._decrypt(row["config_encrypted"]))


def _public(row: sqlite3.Row, owner: str) -> dict:
    payload = _config(row).model_dump(exclude={"enabled", "recipe"})
    return {
        **payload, "id": row["id"], "serverId": row["server_id"], "ownerId": owner,
        "target": row["target"], "image": row["image"], "enabled": bool(row["enabled"]),
        "nextAt": row["next_at"], "pauseReason": row["pause_reason"],
    }


def _rules(server_id: str) -> Iterator[tuple[str, sqlite3.Row]]:
    for owner, _ in list_user_tool_dbs(TOOL_ID):
        with db(owner) as conn:
            rows = conn.execute("SELECT * FROM docker_update_rules WHERE server_id=?", (server_id,)).fetchall()
        yield from ((owner, row) for row in rows)


def _find(server_id: str, rule_id: str) -> tuple[str, sqlite3.Row]:
    for owner, row in _rules(server_id):
        if row["id"] == rule_id:
            return owner, row
    raise error("UPDATE_RULE_NOT_FOUND", "找不到自动更新规则", 404)


def _assert_idle(conn: sqlite3.Connection, rule_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM docker_update_runs WHERE rule_id=? AND status IN (?, ?, ?)", (rule_id, *ACTIVE),
    ).fetchone():
        raise error("UPDATE_IN_PROGRESS", "该规则已有任务正在执行", 409)


def list_rules(server_id: str, user: User) -> list[dict]:
    require_server(server_id, user)
    return [_public(row, owner) for owner, row in _rules(server_id)]


def get_rule(server_id: str, user: User, rule_id: str) -> dict:
    require_server(server_id, user)
    owner, row = _find(server_id, rule_id)
    return {**_public(row, owner), "recipe": _config(row).recipe}


def _verify_detached_runner(server: sqlite3.Row, client, engine: str) -> None:
    probe_id = f"probe-{base._new_id()}"
    path = remote.work_dir(client, probe_id)
    try:
        # Use a separate pool so the test closes its transport without
        # holding two leases from the same server's shared pool.
        with SSHConnectionPool().borrow_client(base._ssh_spec(server)) as probe_client:
            try:
                remote.start_detach_probe(probe_client, probe_id, path)
            finally:
                # Returning a lease to the pool would leave the SSH transport open.
                probe_client.invalidate()
        remote.verify_detach_probe(client, path, engine)
    finally:
        try:
            remote.clean(client, path)
        except remote.RemoteUpdateError:
            logger.warning("Could not clean Docker screen probe %s", probe_id)


def _validate_remote(server: sqlite3.Row, payload: UpdateRuleInput, image: str) -> None:
    try:
        with base._ssh_connect(server) as client:
            engine = remote.preflight(client, payload.mode == "compose")
            _verify_detached_runner(server, client, engine)
            path = remote.work_dir(client, f"validate-{base._new_id()}")
            try:
                remote.snapshot(client, payload, image, path, engine)
            finally:
                if payload.mode == "compose":
                    remote.clean(client, path)
    except remote.RemoteUpdateError as exc:
        raise error("UPDATE_PREFLIGHT_FAILED", STAGES.get(exc.stage, "无法校验远程更新环境"), 422) from exc
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise error("UPDATE_PREFLIGHT_FAILED", STAGES["target_metadata"], 422) from exc


def save_rule(server_id: str, user: User, payload: UpdateRuleInput, rule_id: str | None = None) -> dict:
    server = require_server(server_id, user)
    target, image = rule_target(payload)
    if rule_id is None:
        payload = payload.model_copy(update={"enabled": False})
    elif payload.enabled:
        owner, _ = _find(server_id, rule_id)
        require_server(server_id, _get_user(owner))
        _validate_remote(server, payload, image)

    with lock("auto-update-rules"):
        require_server(server_id, user)
        owner, previous = _find(server_id, rule_id) if rule_id else (user.id, None)
        if previous and target != previous["target"]:
            raise error("UPDATE_TARGET_IMMUTABLE", "请为新的目标创建独立规则", 422)
        if any(row["target"] == target and row["id"] != rule_id for _, row in _rules(server_id)):
            raise error("UPDATE_TARGET_EXISTS", "该目标已有自动更新规则", 409)
        now = base._now()
        rule_id = rule_id or base._new_id()
        with db(owner) as conn:
            _assert_idle(conn, rule_id)
            config = base._encrypt(payload.model_dump_json())
            if previous:
                failed_digest = previous["failed_digest"] if _config(previous).recipe == payload.recipe else ""
                conn.execute("""
                    UPDATE docker_update_rules SET image=?, config_encrypted=?, enabled=?, next_at=?,
                        failed_digest=?, pause_reason='', updated_at=? WHERE id=?
                """, (image, config, payload.enabled, now, failed_digest, now, rule_id))
            else:
                conn.execute("""
                    INSERT INTO docker_update_rules
                        (id, server_id, target, image, config_encrypted, enabled, next_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (rule_id, server_id, target, image, config, payload.enabled, now, now, now))
            row = conn.execute("SELECT * FROM docker_update_rules WHERE id=?", (rule_id,)).fetchone()
    return _public(row, owner)


def delete_rule(server_id: str, user: User, rule_id: str) -> None:
    require_server(server_id, user)
    with lock("auto-update-rules"):
        owner, _ = _find(server_id, rule_id)
        with db(owner) as conn:
            _assert_idle(conn, rule_id)
            conn.execute("DELETE FROM docker_update_rules WHERE id=?", (rule_id,))


def _run_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "status": row["status"], "stage": row["stage"], "message": row["message"],
        "createdAt": row["created_at"], "finishedAt": row["finished_at"], "oldImage": row["old_image"],
        "newImage": row["new_image"], "digest": row["digest"], "startMail": row["start_mail"],
        "finishMail": row["finish_mail"],
    }


def list_runs(server_id: str, user: User, rule_id: str) -> list[dict]:
    require_server(server_id, user)
    owner, _ = _find(server_id, rule_id)
    with db(owner) as conn:
        rows = conn.execute("""
            SELECT * FROM docker_update_runs WHERE rule_id=? ORDER BY created_at DESC LIMIT 100
        """, (rule_id,)).fetchall()
    return [_run_public(row) for row in rows]


def _enqueue(conn: sqlite3.Connection, rule_id: str, manual: bool) -> str:
    _assert_idle(conn, rule_id)
    run_id = base._new_id()
    conn.execute("""
        INSERT INTO docker_update_runs (id, rule_id, status, manual, created_at) VALUES (?, ?, 'queued', ?, ?)
    """, (run_id, rule_id, manual, base._now()))
    return run_id


def request_run(server_id: str, user: User, rule_id: str) -> str:
    require_server(server_id, user)
    with lock("auto-update-rules"):
        owner, _ = _find(server_id, rule_id)
        require_server(server_id, _get_user(owner))
        with db(owner) as conn:
            return _enqueue(conn, rule_id, manual=True)


def _next_time(payload: UpdateRuleInput) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=payload.intervalMinutes)).isoformat()


def _finish(owner: str, rule: sqlite3.Row, run: sqlite3.Row, status: str, stage: str,
            message: str = "") -> None:
    message = message or STAGES.get(stage, "远程更新步骤失败。")
    payload = _config(rule)
    with db(owner) as conn:
        conn.execute("""
            UPDATE docker_update_runs SET status=?, stage=?, message=?, finished_at=?,
                finish_mail=CASE WHEN start_mail!='skipped' THEN 'pending' ELSE 'skipped' END WHERE id=?
        """, (status, stage, message, base._now(), run["id"]))
        conn.execute("UPDATE docker_update_rules SET next_at=? WHERE id=?", (_next_time(payload), rule["id"]))
        if status in {"rolled_back", "needs_review"} or stage == "pull_failed":
            conn.execute("UPDATE docker_update_rules SET failed_digest=? WHERE id=?", (run["digest"], rule["id"]))
        if status == "needs_review" or stage == "permission_revoked":
            conn.execute("UPDATE docker_update_rules SET enabled=0, pause_reason=? WHERE id=?", (message, rule["id"]))
        if status == "succeeded":
            conn.execute("UPDATE docker_update_rules SET failed_digest='', pause_reason='' WHERE id=?", (rule["id"],))


def _mail(owner: str, rule: sqlite3.Row, run_id: str, *, final: bool) -> None:
    column = "finish_mail" if final else "start_mail"
    with db(owner) as conn:
        row = conn.execute("SELECT * FROM docker_update_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        if row[column] == "sending":
            conn.execute(f"UPDATE docker_update_runs SET {column}='unknown' WHERE id=?", (run_id,))
            return
        if row[column] != "pending":
            return
        conn.execute(f"UPDATE docker_update_runs SET {column}='sending' WHERE id=?", (run_id,))
    event = "更新结果" if final else "开始更新"
    body = (
        f"服务器 ID：{rule['server_id']}\n目标：{rule['target']}\n运行 ID：{run_id}\n"
        f"旧镜像：{row['old_image']}\n新镜像：{row['new_image']}\n镜像摘要：{row['digest']}\n"
        f"结果：{row['message'] or event}"
    )
    status = "sent"
    try:
        recipients = json.loads(base._decrypt(row["recipients_encrypted"]))
        if not email_service.send_email(recipients, f"Docker {event} · {rule['target']}", body):
            status = "failed"
    except Exception:
        status = "failed"
        logger.warning("Docker update notification failed for run %s", run_id)
    with db(owner) as conn:
        conn.execute(f"UPDATE docker_update_runs SET {column}=? WHERE id=?", (status, run_id))


def _record_handoff(owner: str, run_id: str, path: str, current: remote.Snapshot,
                    candidate: remote.Candidate, recipients: list[str]) -> None:
    with db(owner) as conn:
        conn.execute("""
            UPDATE docker_update_runs SET status='handoff', stage='handoff', remote_path=?,
                old_ids=?, old_image=?, new_image=?, digest=?, recipients_encrypted=?, start_mail=? WHERE id=?
        """, (path, json.dumps(current.ids), current.image, candidate.image, candidate.manifest,
              base._encrypt(json.dumps(recipients)) if recipients else "",
              "pending" if recipients else "skipped", run_id))


def _prepare(owner: str, rule: sqlite3.Row, run: sqlite3.Row) -> None:
    path = ""
    handed_off = False
    try:
        user = _get_user(owner)
        server = require_server(rule["server_id"], user)
    except ToolboxError:
        _finish(owner, rule, run, "failed", "permission_revoked")
        return
    try:
        payload = _config(rule)
        with base._ssh_connect(server) as client:
            engine = remote.preflight(client, payload.mode == "compose")
            path = remote.work_dir(client, run["id"])
            try:
                current = remote.snapshot(client, payload, rule["image"], path, engine)
                candidate = remote.candidate_image(client, rule["image"], current.platform)
                if candidate.image == current.image:
                    _finish(owner, rule, run, "no_change", "unchanged")
                    return
                if not run["manual"] and candidate.manifest == rule["failed_digest"]:
                    _finish(owner, rule, run, "skipped", "previous_failure")
                    return
                _verify_detached_runner(server, client, engine)
                remote.stage_worker(client, run["id"], path, payload, rule["target"], current, candidate)
                require_server(rule["server_id"], _get_user(owner))
                _record_handoff(owner, run["id"], path, current, candidate, payload.recipients)
                handed_off = True
                _mail(owner, rule, run["id"], final=False)
                try:
                    require_server(rule["server_id"], _get_user(owner))
                except ToolboxError:
                    with db(owner) as conn:
                        handed_off_run = conn.execute("SELECT * FROM docker_update_runs WHERE id=?", (run["id"],)).fetchone()
                    _finish(owner, rule, handed_off_run, "failed", "permission_revoked")
                    handed_off = False
                    return
                remote.launch(client, run["id"], path)
            finally:
                if not handed_off and (payload.mode == "compose" or path):
                    try:
                        remote.clean(client, path)
                    except remote.RemoteUpdateError:
                        logger.warning("Could not clean update workspace for run %s", run["id"])
    except remote.RemoteUpdateError as exc:
        if not handed_off:
            _finish(owner, rule, run, "failed", exc.stage)
    except ToolboxError as exc:
        if not handed_off:
            revoked = exc.status_code in {401, 403, 404}
            _finish(owner, rule, run, "failed", "permission_revoked" if revoked else "validation",
                    "" if revoked else exc.message if exc.code == "INVALID_AUTO_UPDATE" else "无法连接目标服务器。")
    except (KeyError, TypeError, ValueError, IndexError):
        if not handed_off:
            _finish(owner, rule, run, "failed", "target_metadata")


def _move_refs(server_id: str, old: str, new: str) -> None:
    if old == new:
        return
    with get_connection() as conn:
        for previous, current in ((old, new), (old[:12], new[:12])):
            conn.execute("""
                UPDATE OR IGNORE docker_resource_roles SET resource_ref=?
                WHERE server_id=? AND resource_type='container' AND resource_ref=?
            """, (current, server_id, previous))
            conn.execute("""
                DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND resource_ref=?
            """, (server_id, previous))
            conn.execute("""
                UPDATE docker_containers_meta SET is_public=1 WHERE server_id=? AND container_ref=?
                AND EXISTS (SELECT 1 FROM docker_containers_meta WHERE server_id=? AND container_ref=? AND is_public=1)
            """, (server_id, current, server_id, previous))
            conn.execute("""
                UPDATE OR IGNORE docker_containers_meta SET container_ref=? WHERE server_id=? AND container_ref=?
            """, (current, server_id, previous))
            conn.execute("DELETE FROM docker_containers_meta WHERE server_id=? AND container_ref=?", (server_id, previous))
            conn.execute("DELETE FROM docker_container_resource_cache WHERE server_id=? AND container_ref IN (?, ?)",
                         (server_id, previous, current))


def _reconcile(owner: str, rule: sqlite3.Row, run: sqlite3.Row) -> None:
    _mail(owner, rule, run["id"], final=False)
    try:
        with get_connection() as conn:
            server = base._get_server_row(conn, rule["server_id"])
        with base._ssh_connect(server) as client:
            result = remote.outcome(client, run["id"], run["remote_path"])
            if result is None:
                return
            status, stage, replacements = result
            if status in {"succeeded", "rolled_back"}:
                before = json.loads(run["old_ids"])
                if set(replacements) != set(before) or len(set(replacements.values())) != len(before):
                    status, stage = "needs_review", "target_changed"
                else:
                    for old, new in replacements.items():
                        _move_refs(rule["server_id"], old, new)
                    with get_connection() as conn:
                        conn.executemany("""
                            DELETE FROM docker_container_resource_cache
                            WHERE server_id=? AND container_ref=? AND resource_type='image'
                        """, [(rule["server_id"], name) for name in before.values()])
            _finish(owner, rule, run, status, stage)
            if status != "needs_review":
                try:
                    remote.clean(client, run["remote_path"])
                except remote.RemoteUpdateError:
                    logger.warning("Could not clean update workspace for run %s", run["id"])
    except (ToolboxError, remote.RemoteUpdateError):
        return
    if status in {"succeeded", "rolled_back"}:
        base._refresh_docker_df_cache_best_effort(rule["server_id"])


def _schedule(owner: str) -> None:
    with lock("auto-update-rules"), db(owner) as conn:
        rows = conn.execute("""
            SELECT id FROM docker_update_rules WHERE enabled=1 AND next_at<=?
            AND id NOT IN (SELECT rule_id FROM docker_update_runs WHERE status IN (?, ?, ?))
        """, (base._now(), *ACTIVE)).fetchall()
        for row in rows:
            _enqueue(conn, row["id"], manual=False)


def _process_owner(owner: str) -> None:
    _schedule(owner)
    with db(owner) as conn:
        runs = conn.execute("""
            SELECT * FROM docker_update_runs WHERE status IN (?, ?, ?)
            OR finish_mail IN ('pending', 'sending')
            ORDER BY CASE status WHEN 'handoff' THEN 0 ELSE 1 END, created_at
        """, ACTIVE).fetchall()
    for run in runs:
        try:
            with db(owner) as conn:
                rule = conn.execute("SELECT * FROM docker_update_rules WHERE id=?", (run["rule_id"],)).fetchone()
                if rule is None:
                    continue
                if run["status"] == "queued":
                    conn.execute("UPDATE docker_update_runs SET status='checking' WHERE id=?", (run["id"],))
            if run["status"] == "handoff":
                _reconcile(owner, rule, run)
            elif run["status"] in {"queued", "checking"}:
                _prepare(owner, rule, run)
            _mail(owner, rule, run["id"], final=True)
        except Exception:
            logger.exception("Could not process Docker update run %s", run["id"])


def process_updates() -> None:
    with lock("auto-update-scheduler", nonblocking=True) as acquired:
        if acquired:
            for owner, _ in list_user_tool_dbs(TOOL_ID):
                try:
                    _process_owner(owner)
                except Exception:
                    logger.exception("Could not process a Docker update database")
