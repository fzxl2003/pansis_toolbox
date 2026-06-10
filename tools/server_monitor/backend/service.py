from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shlex
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection
from backend.app.services.auth_service import User

TOOL_ID = "server_monitor"
SAMPLE_SECONDS = 30
RETENTION_DAYS = 30
DEFAULT_DIRECTORY_REFRESH_SECONDS = 300


def init_monitor_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS monitor_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                ssh_username TEXT NOT NULL,
                ssh_password_encrypted TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                owner_user_id TEXT,
                directory_whitelist TEXT NOT NULL DEFAULT '[]',
                directory_refresh_seconds INTEGER NOT NULL DEFAULT 300,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_samples (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                cpu_percent REAL,
                memory_total_bytes INTEGER,
                memory_used_bytes INTEGER,
                disks_json TEXT NOT NULL DEFAULT '[]',
                gpus_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                FOREIGN KEY(server_id) REFERENCES monitor_servers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_monitor_samples_server_time
                ON monitor_samples(server_id, collected_at);

            CREATE TABLE IF NOT EXISTS monitor_directory_cache (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                path TEXT NOT NULL,
                total_bytes INTEGER,
                used_bytes INTEGER,
                free_bytes INTEGER,
                refreshed_at TEXT,
                error TEXT,
                UNIQUE(server_id, user_id, path),
                FOREIGN KEY(server_id) REFERENCES monitor_servers(id)
            );
            """
        )


def list_servers(user: User | None) -> list[dict[str, Any]]:
    init_monitor_database()
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM monitor_servers WHERE enabled = 1 ORDER BY is_default DESC, name").fetchall()
    return [_public_server(row) for row in rows if _can_view(row, user)]


def get_server(server_id: str, user: User | None) -> sqlite3.Row:
    init_monitor_database()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM monitor_servers WHERE id = ? AND enabled = 1", (server_id,)).fetchone()
    if row is None or not _can_view(row, user):
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


def create_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    init_monitor_database()
    is_default = bool(payload.get("isDefault")) and user.role == "admin"
    if bool(payload.get("isDefault")) and user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以创建默认服务器", status_code=403, tool_id=TOOL_ID)
    server_id = secrets.token_hex(12)
    now = now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO monitor_servers (
                id, name, host, port, ssh_username, ssh_password_encrypted, is_default, owner_user_id,
                directory_whitelist, directory_refresh_seconds, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                server_id,
                _required(payload, "name"),
                _required(payload, "host"),
                int(payload.get("port") or 22),
                _required(payload, "sshUsername"),
                encrypt_secret(_required(payload, "sshPassword")),
                1 if is_default else 0,
                None if is_default else user.id,
                json.dumps(_clean_paths(payload.get("directoryWhitelist") or []), ensure_ascii=False),
                int(payload.get("directoryRefreshSeconds") or DEFAULT_DIRECTORY_REFRESH_SECONDS),
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM monitor_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def update_server(server_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    _require_edit(row, user)
    next_default = bool(payload.get("isDefault", bool(row["is_default"])))
    if next_default and user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以配置默认服务器", status_code=403, tool_id=TOOL_ID)
    owner_user_id = None if next_default else row["owner_user_id"] or user.id
    password = payload.get("sshPassword")
    encrypted_password = encrypt_secret(password) if password else row["ssh_password_encrypted"]
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE monitor_servers
            SET name = ?, host = ?, port = ?, ssh_username = ?, ssh_password_encrypted = ?,
                is_default = ?, owner_user_id = ?, directory_whitelist = ?,
                directory_refresh_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("name", row["name"]),
                payload.get("host", row["host"]),
                int(payload.get("port") or row["port"]),
                payload.get("sshUsername", row["ssh_username"]),
                encrypted_password,
                1 if next_default else 0,
                owner_user_id,
                json.dumps(_clean_paths(payload.get("directoryWhitelist", _json_list(row["directory_whitelist"]))), ensure_ascii=False),
                int(payload.get("directoryRefreshSeconds") or row["directory_refresh_seconds"]),
                now_iso(),
                server_id,
            ),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM monitor_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(updated)


def delete_server(server_id: str, user: User) -> None:
    row = get_server(server_id, user)
    _require_edit(row, user)
    with get_connection() as connection:
        connection.execute("UPDATE monitor_servers SET enabled = 0, updated_at = ? WHERE id = ?", (now_iso(), server_id))
        connection.commit()


def collect_snapshot(server_id: str, user: User | None, force: bool = False) -> dict[str, Any]:
    row = get_server(server_id, user)
    latest = latest_sample(server_id)
    if latest and not force and _age_seconds(latest["collected_at"]) < SAMPLE_SECONDS:
        return _sample_payload(row, latest)
    sample = _collect_and_store(row)
    prune_history()
    return _sample_payload(row, sample)


def latest_sample(server_id: str) -> sqlite3.Row | None:
    init_monitor_database()
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM monitor_samples WHERE server_id = ? ORDER BY collected_at DESC LIMIT 1",
            (server_id,),
        ).fetchone()


def history(server_id: str, user: User | None, hours: int = 24) -> dict[str, Any]:
    row = get_server(server_id, user)
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * RETENTION_DAYS)))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM monitor_samples
            WHERE server_id = ? AND collected_at >= ?
            ORDER BY collected_at ASC
            """,
            (server_id, since.isoformat()),
        ).fetchall()
    return {"server": _public_server(row), "samples": [_history_sample(item) for item in rows]}


def directory_usage(server_id: str, path: str, user: User | None) -> dict[str, Any]:
    row = get_server(server_id, user)
    clean_path = _clean_path(path)
    if not _path_allowed(clean_path, _json_list(row["directory_whitelist"])):
        raise ToolboxError("DIRECTORY_NOT_ALLOWED", "目录不在服务器白名单范围内", status_code=403, tool_id=TOOL_ID)
    cache_user_id = user.id if user else "anonymous"
    cached = _directory_cache(server_id, cache_user_id, clean_path)
    refresh_seconds = max(10, int(row["directory_refresh_seconds"]))
    if cached and cached["refreshed_at"] and _age_seconds(cached["refreshed_at"]) < refresh_seconds:
        return _directory_payload(row, cached)
    result = _collect_directory(row, clean_path, cache_user_id)
    return _directory_payload(row, result)


def list_directory_usages(server_id: str, user: User | None) -> dict[str, Any]:
    row = get_server(server_id, user)
    cache_user_id = user.id if user else "anonymous"
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM monitor_directory_cache
            WHERE server_id = ? AND user_id = ?
            ORDER BY path ASC
            """,
            (server_id, cache_user_id),
        ).fetchall()
    return {"server": _public_server(row), "directories": [_directory_payload(row, item) for item in rows]}


def delete_directory_usage(server_id: str, path: str, user: User | None) -> None:
    row = get_server(server_id, user)
    cache_user_id = user.id if user else "anonymous"
    clean_path = _clean_path(path)
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM monitor_directory_cache WHERE server_id = ? AND user_id = ? AND path = ?",
            (row["id"], cache_user_id, clean_path),
        )
        connection.commit()


def kill_gpu_process(server_id: str, pid: int, user: User) -> dict[str, Any]:
    row = get_server(server_id, user)
    _require_edit(row, user)
    if pid <= 0:
        raise ToolboxError("INVALID_PID", "进程号不合法", status_code=400, tool_id=TOOL_ID)
    output = _run_ssh(row, f"kill -TERM -- {int(pid)} && echo killed", timeout=10)
    return {"killed": True, "pid": pid, "output": output.strip()}


def collect_due_servers() -> None:
    init_monitor_database()
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM monitor_servers WHERE enabled = 1").fetchall()
    for row in rows:
        latest = latest_sample(row["id"])
        if latest is None or _age_seconds(latest["collected_at"]) >= SAMPLE_SECONDS:
            _collect_and_store(row)
        _refresh_due_directories(row)
    prune_history()


def parse_monitor_output(output: str) -> dict[str, Any]:
    sections: dict[str, list[str]] = {"cpu": [], "meminfo": [], "df": [], "gpu": [], "gpu_processes": [], "proc_stats": [], "gpu_pmon": []}
    current: str | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "__CPU__":
            current = "cpu"
            continue
        if line == "__MEMINFO__":
            current = "meminfo"
            continue
        if line == "__DF__":
            current = "df"
            continue
        if line == "__GPU__":
            current = "gpu"
            continue
        if line == "__GPU_PROCESSES__":
            current = "gpu_processes"
            continue
        if line == "__PROC_STATS__":
            current = "proc_stats"
            continue
        if line == "__GPU_PMON__":
            current = "gpu_pmon"
            continue
        if current and line:
            sections[current].append(line)
    memory_total, memory_used = _parse_meminfo(sections["meminfo"])
    gpus = _parse_gpus(sections["gpu"])
    _attach_gpu_processes(gpus, sections["gpu_processes"], sections["proc_stats"], sections["gpu_pmon"])
    return {
        "cpuPercent": _parse_cpu_percent(sections["cpu"]),
        "memoryTotalBytes": memory_total,
        "memoryUsedBytes": memory_used,
        "disks": _parse_df(sections["df"]),
        "gpus": gpus,
    }


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "SSH 密码无法解密，请重新保存服务器配置", status_code=400, tool_id=TOOL_ID) from exc


def _collect_and_store(row: sqlite3.Row) -> sqlite3.Row:
    error = None
    parsed = {"cpuPercent": None, "memoryTotalBytes": None, "memoryUsedBytes": None, "disks": [], "gpus": []}
    try:
        output = _run_ssh(row, _monitor_command())
        parsed = parse_monitor_output(output)
    except Exception as exc:  # noqa: BLE001 - each server records its own collection error.
        error = str(exc)
    sample_id = secrets.token_hex(12)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO monitor_samples (
                id, server_id, collected_at, cpu_percent, memory_total_bytes, memory_used_bytes,
                disks_json, gpus_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample_id,
                row["id"],
                now_iso(),
                parsed["cpuPercent"],
                parsed["memoryTotalBytes"],
                parsed["memoryUsedBytes"],
                json.dumps(parsed["disks"], ensure_ascii=False),
                json.dumps(parsed["gpus"], ensure_ascii=False),
                error,
            ),
        )
        connection.commit()
        return connection.execute("SELECT * FROM monitor_samples WHERE id = ?", (sample_id,)).fetchone()


def _collect_directory(row: sqlite3.Row, path: str, user_id: str) -> sqlite3.Row:
    error = None
    total = used = free = None
    try:
        command = f"df -B1 -P {shlex.quote(path)} | tail -1; du -sb {shlex.quote(path)} 2>/dev/null | tail -1"
        output = _run_ssh(row, command, timeout=45)
        total, used, free = _parse_directory_output(output)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    with get_connection() as connection:
        existing = _directory_cache(row["id"], user_id, path)
        cache_id = existing["id"] if existing else secrets.token_hex(12)
        connection.execute(
            """
            INSERT INTO monitor_directory_cache (
                id, server_id, user_id, path, total_bytes, used_bytes, free_bytes, refreshed_at, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id, user_id, path) DO UPDATE SET
                total_bytes = excluded.total_bytes,
                used_bytes = excluded.used_bytes,
                free_bytes = excluded.free_bytes,
                refreshed_at = excluded.refreshed_at,
                error = excluded.error
            """,
            (cache_id, row["id"], user_id, path, total, used, free, now_iso(), error),
        )
        connection.commit()
        return connection.execute(
            "SELECT * FROM monitor_directory_cache WHERE server_id = ? AND user_id = ? AND path = ?",
            (row["id"], user_id, path),
        ).fetchone()


def _run_ssh(row: sqlite3.Row, command: str, timeout: int = 20) -> str:
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖，无法执行 SSH 采集", status_code=500, tool_id=TOOL_ID) from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=row["host"],
            port=int(row["port"]),
            username=row["ssh_username"],
            password=decrypt_secret(row["ssh_password_encrypted"]),
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace").strip()
        if error and not output:
            raise ToolboxError("SSH_COMMAND_FAILED", error[:300], status_code=502, tool_id=TOOL_ID)
        return output
    finally:
        client.close()


def _monitor_command() -> str:
    return r"""
echo __CPU__
grep '^cpu ' /proc/stat
sleep 0.2
grep '^cpu ' /proc/stat
echo __MEMINFO__
cat /proc/meminfo
echo __DF__
df -B1 -P
echo __GPU__
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,uuid,name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null || true
  echo __GPU_PROCESSES__
  nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true
  echo __PROC_STATS__
  for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | sort -u); do
    ps -p "$pid" -o pid=,user=,pcpu=,pmem=,comm=,args= 2>/dev/null || true
  done
  echo __GPU_PMON__
  nvidia-smi pmon -c 1 -s um 2>/dev/null | tail -n +3 || true
fi
"""


def _parse_cpu_percent(lines: list[str]) -> float | None:
    if len(lines) < 2:
        return None
    first = _cpu_numbers(lines[0])
    second = _cpu_numbers(lines[1])
    if not first or not second:
        return None
    idle_delta = (second[3] + second[4]) - (first[3] + first[4])
    total_delta = sum(second) - sum(first)
    if total_delta <= 0:
        return None
    return round((1 - idle_delta / total_delta) * 100, 2)


def _cpu_numbers(line: str) -> list[int]:
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return []
    return [int(part) for part in parts[1:] if part.isdigit()]


def _parse_meminfo(lines: list[str]) -> tuple[int | None, int | None]:
    values: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        number = rest.strip().split()[0]
        if number.isdigit():
            values[key] = int(number) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable", values.get("MemFree"))
    used = total - available if total is not None and available is not None else None
    return total, used


def _parse_df(lines: list[str]) -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        disks.append(
            {
                "filesystem": parts[0],
                "totalBytes": int(parts[1]),
                "usedBytes": int(parts[2]),
                "freeBytes": int(parts[3]),
                "mountPath": parts[5],
            }
        )
    return disks


def _parse_gpus(lines: list[str]) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        has_uuid = len(parts) >= 8
        gpus.append(
            {
                "index": int(parts[0]) if parts[0].isdigit() else len(gpus),
                "uuid": parts[1] if has_uuid else "",
                "name": parts[2] if has_uuid else parts[1],
                "utilizationPercent": _float_or_none(parts[3] if has_uuid else parts[2]),
                "memoryTotalMiB": _float_or_none(parts[4] if has_uuid else parts[3]),
                "memoryUsedMiB": _float_or_none(parts[5] if has_uuid else parts[4]),
                "temperatureC": _float_or_none(parts[6] if has_uuid else parts[5]),
                "powerW": _float_or_none(parts[7] if has_uuid else parts[6]),
                "processCount": 0,
                "processes": [],
            }
        )
    return gpus


def _attach_gpu_processes(
    gpus: list[dict[str, Any]],
    process_lines: list[str],
    stat_lines: list[str],
    pmon_lines: list[str],
) -> None:
    by_uuid = {gpu["uuid"]: gpu for gpu in gpus}
    stats = _parse_process_stats(stat_lines)
    pmon = _parse_gpu_pmon(pmon_lines)
    for line in process_lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        gpu = by_uuid.get(parts[0])
        if gpu is None:
            continue
        pid = int(parts[1]) if parts[1].isdigit() else None
        if pid is None:
            continue
        stat = stats.get(pid, {})
        pmon_stat = pmon.get((gpu["index"], pid), {})
        process = {
            "pid": pid,
            "name": parts[2] or stat.get("name") or "unknown",
            "username": stat.get("username"),
            "command": stat.get("command") or parts[2],
            "usedMemoryMiB": _float_or_none(parts[3]),
            "cpuPercent": stat.get("cpuPercent"),
            "memoryPercent": stat.get("memoryPercent"),
            "gpuPercent": pmon_stat.get("gpuPercent"),
            "gpuMemoryPercent": pmon_stat.get("gpuMemoryPercent"),
        }
        gpu["processes"].append(process)
    for gpu in gpus:
        gpu["processCount"] = len(gpu["processes"])


def _parse_process_stats(lines: list[str]) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    for line in lines:
        parts = line.split(maxsplit=5)
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        stats[int(parts[0])] = {
            "username": parts[1],
            "cpuPercent": _float_or_none(parts[2]),
            "memoryPercent": _float_or_none(parts[3]),
            "name": parts[4],
            "command": parts[5] if len(parts) >= 6 else parts[4],
        }
    return stats


def _parse_gpu_pmon(lines: list[str]) -> dict[tuple[int, int], dict[str, Any]]:
    stats: dict[tuple[int, int], dict[str, Any]] = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 5 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        stats[(int(parts[0]), int(parts[1]))] = {
            "gpuPercent": _float_or_none(parts[3].replace("-", "")),
            "gpuMemoryPercent": _float_or_none(parts[4].replace("-", "")),
        }
    return stats


def _parse_directory_output(output: str) -> tuple[int | None, int | None, int | None]:
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, None, None
    df_parts = lines[0].split()
    du_parts = lines[1].split()
    total = int(df_parts[1]) if len(df_parts) >= 4 and df_parts[1].isdigit() else None
    free = int(df_parts[3]) if len(df_parts) >= 4 and df_parts[3].isdigit() else None
    used = int(du_parts[0]) if du_parts and du_parts[0].isdigit() else None
    return total, used, free


def _directory_cache(server_id: str, user_id: str, path: str) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM monitor_directory_cache WHERE server_id = ? AND user_id = ? AND path = ?",
            (server_id, user_id, path),
        ).fetchone()


def _refresh_due_directories(row: sqlite3.Row) -> None:
    refresh_seconds = max(10, int(row["directory_refresh_seconds"]))
    with get_connection() as connection:
        directories = connection.execute(
            "SELECT * FROM monitor_directory_cache WHERE server_id = ?",
            (row["id"],),
        ).fetchall()
    for directory in directories:
        if directory["refreshed_at"] is None or _age_seconds(directory["refreshed_at"]) >= refresh_seconds:
            _collect_directory(row, directory["path"], directory["user_id"])


def _public_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "sshUsername": row["ssh_username"],
        "isDefault": bool(row["is_default"]),
        "ownerUserId": row["owner_user_id"],
        "directoryWhitelist": _json_list(row["directory_whitelist"]),
        "directoryRefreshSeconds": row["directory_refresh_seconds"],
        "updatedAt": row["updated_at"],
    }


def _sample_payload(server: sqlite3.Row, sample: sqlite3.Row) -> dict[str, Any]:
    return {"server": _public_server(server), "sample": _history_sample(sample)}


def _history_sample(sample: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": sample["id"],
        "serverId": sample["server_id"],
        "collectedAt": sample["collected_at"],
        "cpuPercent": sample["cpu_percent"],
        "memoryTotalBytes": sample["memory_total_bytes"],
        "memoryUsedBytes": sample["memory_used_bytes"],
        "disks": json.loads(sample["disks_json"]),
        "gpus": json.loads(sample["gpus_json"]),
        "error": sample["error"],
    }


def _directory_payload(server: sqlite3.Row, cache: sqlite3.Row) -> dict[str, Any]:
    return {
        "server": _public_server(server),
        "path": cache["path"],
        "totalBytes": cache["total_bytes"],
        "usedBytes": cache["used_bytes"],
        "freeBytes": cache["free_bytes"],
        "refreshedAt": cache["refreshed_at"],
        "error": cache["error"],
    }


def _can_view(row: sqlite3.Row, user: User | None) -> bool:
    if row["is_default"]:
        return True
    return user is not None and row["owner_user_id"] == user.id


def _require_edit(row: sqlite3.Row, user: User) -> None:
    if row["is_default"] and user.role == "admin":
        return
    if not row["is_default"] and row["owner_user_id"] == user.id:
        return
    raise ToolboxError("SERVER_FORBIDDEN", "没有权限编辑该服务器", status_code=403, tool_id=TOOL_ID)


def _path_allowed(path: str, whitelist: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in whitelist)


def _clean_paths(paths: list[str]) -> list[str]:
    cleaned = []
    for path in paths:
        clean = _clean_path(path)
        if clean not in cleaned:
            cleaned.append(clean)
    return cleaned


def _clean_path(path: str) -> str:
    if not path or not str(path).startswith("/"):
        raise ToolboxError("INVALID_DIRECTORY", "目录必须是绝对路径", status_code=400, tool_id=TOOL_ID)
    clean = str(PurePosixPath(str(path)))
    if clean == "/":
        return clean
    return clean.rstrip("/")


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ToolboxError("INVALID_SERVER", "服务器名称、地址、SSH 用户名和密码不能为空", status_code=400, tool_id=TOOL_ID)
    return value


def _age_seconds(iso_value: str) -> float:
    return time.time() - datetime.fromisoformat(iso_value).timestamp()


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().session_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def prune_history() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    with get_connection() as connection:
        connection.execute("DELETE FROM monitor_samples WHERE collected_at < ?", (cutoff.isoformat(),))
        connection.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
