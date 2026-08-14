"""Remote-tail TensorBoard progress collection and ETA calculations.

Event files are never copied to the toolbox host.  ``REMOTE_COLLECTOR_SOURCE``
is sent to ``python3 -`` over the already-authenticated SSH channel and emits a
small JSON summary only.
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import list_user_tool_dbs, user_tool_connection_context
from backend.app.services import ssh_connection_service
from backend.app.services.auth_service import User
from backend.app.services.data_management import DataCategory, register_tool_categories
from backend.app.services.ssh_connection_service import SSHConnectionSpec

TOOL_ID = "tensorboard_progress_monitor"
DEFAULT_INLINE_YAML = """tensorboard_root: /path/to/tensorboard/logs
progress_tag: train/step
progress_mode: event_step
tail_bytes: 1048576
report_interval_seconds: 60
rate_report_count: 5
stale_after_seconds: 180
overall_concurrency: 1
groups:
  - name: all
    pattern: "*"
    target_step: 1000000
    total_runs:
    children: []
"""
MAX_YAML_BYTES = 1_048_576
_initialized_dbs: set[str] = set()
_task_locks: dict[tuple[str, str], threading.Lock] = {}
_task_locks_guard = threading.Lock()
logger = logging.getLogger(__name__)


# This source deliberately relies only on the remote Python standard library.
# It understands enough TFRecord/protobuf wire format for TensorBoard scalar
# summaries, including the common TensorProto scalar representation.
REMOTE_COLLECTOR_SOURCE = r'''import base64, json, os, re, struct, sys, time

def _varint(buf, pos):
    value = 0
    shift = 0
    while pos < len(buf) and shift < 70:
        b = buf[pos]; pos += 1
        value |= (b & 127) << shift
        if not (b & 128): return value, pos
        shift += 7
    raise ValueError("invalid varint")

def _fields(buf):
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            if pos + 8 > len(buf): raise ValueError("truncated fixed64")
            value = buf[pos:pos + 8]; pos += 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            if length < 0 or pos + length > len(buf): raise ValueError("truncated bytes")
            value = buf[pos:pos + length]; pos += length
        elif wire == 5:
            if pos + 4 > len(buf): raise ValueError("truncated fixed32")
            value = buf[pos:pos + 4]; pos += 4
        else:
            raise ValueError("unsupported wire type")
        yield number, wire, value

_CRC_TABLE = None
def _crc32c(data):
    global _CRC_TABLE
    if _CRC_TABLE is None:
        table = []
        for n in range(256):
            c = n
            for _ in range(8): c = (c >> 1) ^ (0x82F63B78 if c & 1 else 0)
            table.append(c)
        _CRC_TABLE = table
    c = 0xffffffff
    for b in data: c = _CRC_TABLE[(c ^ b) & 255] ^ (c >> 8)
    return (~c) & 0xffffffff

def _masked_crc(data):
    c = _crc32c(data)
    return (((c >> 15) | (c << 17)) + 0xA282EAD8) & 0xffffffff

def _tensor_scalar(data):
    dtype = None; content = None; values = {}
    for num, wire, value in _fields(data):
        if num == 1 and wire == 0: dtype = value
        elif num == 4 and wire == 2: content = value
        elif num in (5, 6, 7, 10, 12, 13) and wire in (0, 1, 2, 5): values.setdefault(num, value)
    try:
        if content:
            if dtype == 1 and len(content) >= 4: return float(struct.unpack('<f', content[:4])[0])
            if dtype == 2 and len(content) >= 8: return float(struct.unpack('<d', content[:8])[0])
            if dtype in (3, 9, 22) and len(content) >= 4: return float(struct.unpack('<i', content[:4])[0])
            if dtype in (4, 10) and len(content) >= 8: return float(struct.unpack('<q', content[:8])[0])
        value = values.get(5) or values.get(6) or values.get(7) or values.get(10) or values.get(12) or values.get(13)
        if isinstance(value, bytes):
            return float(struct.unpack('<d' if len(value) == 8 else '<f', value)[0])
        if value is not None: return float(value)
    except (ValueError, struct.error, OverflowError): pass
    return None

def _summary_value(summary, wanted):
    for num, wire, raw in _fields(summary):
        if num != 1 or wire != 2: continue
        tag = None; scalar = None; tensor = None
        for vnum, vwire, value in _fields(raw):
            if vnum == 1 and vwire == 2: tag = value.decode('utf-8', 'replace')
            elif vnum == 2 and vwire == 5: scalar = struct.unpack('<f', value)[0]
            elif vnum == 8 and vwire == 2: tensor = value
        if tag == wanted:
            return scalar if scalar is not None else (_tensor_scalar(tensor) if tensor else None)
    return None

def _event_progress(payload, tag, mode):
    wall = None; step = None; summary = None
    for num, wire, value in _fields(payload):
        if num == 1 and wire == 1: wall = struct.unpack('<d', value)[0]
        elif num == 2 and wire == 0: step = value
        elif num == 5 and wire == 2: summary = value
    if wall is None or summary is None: return None
    scalar = _summary_value(summary, tag)
    if scalar is None: return None
    progress = float(step) if mode == 'event_step' and step is not None else scalar
    return {'progress': progress, 'event_time': wall, 'scalar_value': scalar}

def _tail_events(path, tail_bytes, tag, mode):
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        fh.seek(max(0, size - tail_bytes)); data = fh.read(tail_bytes)
    pos = 0; latest = None
    while pos + 16 <= len(data):
        length = struct.unpack_from('<Q', data, pos)[0]
        end = pos + 12 + length + 4
        if length > len(data) or end > len(data): pos += 1; continue
        length_raw = data[pos:pos + 8]
        if struct.unpack_from('<I', data, pos + 8)[0] != _masked_crc(length_raw): pos += 1; continue
        payload = data[pos + 12:pos + 12 + length]
        if struct.unpack_from('<I', data, pos + 12 + length)[0] != _masked_crc(payload): pos += 1; continue
        try: item = _event_progress(payload, tag, mode)
        except (ValueError, struct.error, UnicodeError): item = None
        if item and (latest is None or item['event_time'] >= latest['event_time']): latest = item
        pos = end
    return latest

def main():
    encoded = sys.argv[sys.argv.index('--request-base64') + 1]
    request = json.loads(base64.b64decode(encoded).decode('utf-8'))
    root = request['root']; tail = int(request['tail_bytes']); tag = request['progress_tag']; mode = request['progress_mode']
    previous = request.get('previous_files', {})
    if not os.path.isdir(root): raise RuntimeError('TensorBoard 根目录不存在或不可访问: ' + root)
    runs = {}; errors = []; file_states = []; read_file_count = 0; skipped_file_count = 0
    for directory, _, files in os.walk(root):
        names = [n for n in files if n.startswith('events.out.tfevents.')]
        if not names: continue
        rel = os.path.relpath(directory, root).replace(os.sep, '/')
        if rel == '.': rel = '.'
        state = runs.setdefault(rel, {'relative_path': rel, 'start_hint': None, 'latest': None, 'event_file_count': 0, 'last_file_mtime': None})
        for name in names:
            path = os.path.join(directory, name); state['event_file_count'] += 1
            file_key = name if rel == '.' else rel + '/' + name
            match = re.search(r'events\.out\.tfevents\.(\d+)', name)
            if match:
                hint = float(match.group(1))
                state['start_hint'] = hint if state['start_hint'] is None else min(state['start_hint'], hint)
            try:
                stat = os.stat(path)
                signature = {'size': int(stat.st_size), 'mtime_ns': int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1000000000)))}
                file_states.append({'path': file_key, **signature})
                state['last_file_mtime'] = stat.st_mtime if state['last_file_mtime'] is None else max(state['last_file_mtime'], stat.st_mtime)
                prior = previous.get(file_key)
                if prior and int(prior.get('size', -1)) == signature['size'] and int(prior.get('mtime_ns', -1)) == signature['mtime_ns']:
                    skipped_file_count += 1
                    continue
                read_file_count += 1
                item = _tail_events(path, tail, tag, mode)
                if item and (state['latest'] is None or item['event_time'] >= state['latest']['event_time']): state['latest'] = item
            except Exception as exc:
                errors.append({'path': (rel + '/' + name)[:500], 'error': str(exc)[:240]})
    print(json.dumps({'runs': list(runs.values()), 'files': file_states, 'errors': errors, 'read_file_count': read_file_count, 'skipped_file_count': skipped_file_count}, separators=(',', ':'), allow_nan=False))

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:500]}))
        sys.exit(2)
'''


def init_database(user_id: str) -> None:
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tpm_servers (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, name TEXT NOT NULL,
          host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 22, ssh_username TEXT NOT NULL,
          ssh_password_encrypted TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tpm_tasks (
          id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, server_id TEXT NOT NULL,
          name TEXT NOT NULL, config_source TEXT NOT NULL DEFAULT 'inline', inline_yaml TEXT NOT NULL DEFAULT '',
          remote_yaml_path TEXT NOT NULL DEFAULT '', python_command TEXT NOT NULL DEFAULT 'python3',
          report_interval_seconds INTEGER NOT NULL DEFAULT 60, enabled INTEGER NOT NULL DEFAULT 1,
          show_in_tabs INTEGER NOT NULL DEFAULT 1, display_order INTEGER NOT NULL DEFAULT 0,
          last_report_at TEXT, last_config_json TEXT NOT NULL DEFAULT '', last_yaml_hash TEXT NOT NULL DEFAULT '', last_config_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tpm_reports (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, reported_at TEXT NOT NULL, success INTEGER NOT NULL,
          config_hash TEXT NOT NULL DEFAULT '', summary_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(task_id) REFERENCES tpm_tasks(id));
        CREATE TABLE IF NOT EXISTS tpm_run_samples (
          id TEXT PRIMARY KEY, report_id TEXT NOT NULL, task_id TEXT NOT NULL, reported_at TEXT NOT NULL, run_key TEXT NOT NULL,
          relative_path TEXT NOT NULL, group_name TEXT, progress REAL, target_step REAL,
          event_time REAL, started_at REAL, status TEXT NOT NULL, rate_per_second REAL,
          eta_seconds REAL, duration_seconds REAL, error TEXT NOT NULL DEFAULT '', file_mtime REAL,
          FOREIGN KEY(report_id) REFERENCES tpm_reports(id));
        CREATE TABLE IF NOT EXISTS tpm_event_files (
          task_id TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
          last_seen_at TEXT NOT NULL, PRIMARY KEY(task_id, path));
        CREATE INDEX IF NOT EXISTS idx_tpm_tasks_due ON tpm_tasks(enabled, last_report_at);
        CREATE INDEX IF NOT EXISTS idx_tpm_reports_task_time ON tpm_reports(task_id, reported_at);
        CREATE INDEX IF NOT EXISTS idx_tpm_samples_task_run ON tpm_run_samples(task_id, run_key, report_id);
        """)
        try:
            conn.execute("SELECT reported_at FROM tpm_run_samples LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tpm_run_samples ADD COLUMN reported_at TEXT NOT NULL DEFAULT ''")
        try:
            conn.execute("SELECT file_mtime FROM tpm_run_samples LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tpm_run_samples ADD COLUMN file_mtime REAL")
        try:
            conn.execute("SELECT show_in_tabs FROM tpm_tasks LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tpm_tasks ADD COLUMN show_in_tabs INTEGER NOT NULL DEFAULT 1")
        try:
            conn.execute("SELECT display_order FROM tpm_tasks LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tpm_tasks ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")
            rows = conn.execute("SELECT id FROM tpm_tasks ORDER BY created_at ASC, id ASC").fetchall()
            for index, row in enumerate(rows):
                conn.execute("UPDATE tpm_tasks SET display_order=? WHERE id=?", (index, row["id"]))
        try:
            conn.execute("SELECT last_yaml_hash FROM tpm_tasks LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tpm_tasks ADD COLUMN last_yaml_hash TEXT NOT NULL DEFAULT ''")
    _initialized_dbs.add(user_id)


register_tool_categories(TOOL_ID, [
    DataCategory(name="config", tables=["tpm_servers", "tpm_tasks"], time_column=None, description="服务器和监控任务配置", storage="user_tool_db"),
    DataCategory(name="reports", tables=["tpm_reports", "tpm_run_samples"], time_column="reported_at", description="TensorBoard 进度报表", storage="user_tool_db"),
    DataCategory(name="file_cache", tables=["tpm_event_files"], time_column="last_seen_at", description="远端 event 文件状态缓存", storage="user_tool_db"),
])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet() -> Fernet:
    secret = get_settings().session_secret.encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ToolboxError("INVALID_SECRET", "无法解密 SSH 密码", status_code=400, tool_id=TOOL_ID) from exc


def _server_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"], "sshUsername": row["ssh_username"], "enabled": bool(row["enabled"]), "updatedAt": row["updated_at"]}


def _task_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "serverId": row["server_id"], "configSource": row["config_source"], "inlineYaml": row["inline_yaml"], "remoteYamlPath": row["remote_yaml_path"], "pythonCommand": row["python_command"], "reportIntervalSeconds": row["report_interval_seconds"], "enabled": bool(row["enabled"]), "showInTabs": bool(row["show_in_tabs"]), "displayOrder": row["display_order"], "lastReportAt": row["last_report_at"], "lastConfigError": row["last_config_error"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def list_servers(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_server_public(r) for r in conn.execute("SELECT * FROM tpm_servers WHERE owner_user_id=? ORDER BY name", (user.id,)).fetchall()]


def create_server(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    if not data["name"].strip() or not data["host"].strip() or not data["sshUsername"].strip() or not data.get("sshPassword"):
        raise ToolboxError("INVALID_SERVER", "服务器名称、地址、用户名和密码不能为空", status_code=400, tool_id=TOOL_ID)
    row = {"id": secrets.token_urlsafe(12), "now": now_iso(), **data}
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("INSERT INTO tpm_servers VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)", (row["id"], user.id, row["name"].strip(), row["host"].strip(), int(row["port"]), row["sshUsername"].strip(), encrypt_secret(row["sshPassword"]), row["now"], row["now"]))
        conn.commit(); saved = conn.execute("SELECT * FROM tpm_servers WHERE id=?", (row["id"],)).fetchone()
    return _server_public(saved)


def update_server(server_id: str, data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = _owned_server(conn, server_id, user.id)
        password = data.get("sshPassword") or decrypt_secret(row["ssh_password_encrypted"])
        conn.execute("UPDATE tpm_servers SET name=?,host=?,port=?,ssh_username=?,ssh_password_encrypted=?,updated_at=? WHERE id=?", (data["name"].strip(), data["host"].strip(), int(data["port"]), data["sshUsername"].strip(), encrypt_secret(password), now_iso(), server_id))
        conn.commit(); return _server_public(conn.execute("SELECT * FROM tpm_servers WHERE id=?", (server_id,)).fetchone())


def delete_server(server_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        _owned_server(conn, server_id, user.id)
        if conn.execute("SELECT 1 FROM tpm_tasks WHERE server_id=? LIMIT 1", (server_id,)).fetchone():
            raise ToolboxError("SERVER_IN_USE", "该服务器仍被监控任务使用", status_code=409, tool_id=TOOL_ID)
        conn.execute("DELETE FROM tpm_servers WHERE id=?", (server_id,)); conn.commit()


def _owned_server(conn: sqlite3.Connection, server_id: str, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tpm_servers WHERE id=? AND owner_user_id=?", (server_id, user_id)).fetchone()
    if not row: raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在", status_code=404, tool_id=TOOL_ID)
    return row


def _owned_task(conn: sqlite3.Connection, task_id: str, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tpm_tasks WHERE id=? AND owner_user_id=?", (task_id, user_id)).fetchone()
    if not row: raise ToolboxError("TASK_NOT_FOUND", "监控任务不存在", status_code=404, tool_id=TOOL_ID)
    return row


def _validate_python_command(command: str) -> str:
    command = command.strip() or "python3"
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", command):
        raise ToolboxError("INVALID_PYTHON_COMMAND", "Python 命令只能包含路径安全字符", status_code=400, tool_id=TOOL_ID)
    return command


def create_task(data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id)
    _validate_task_input(data)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        _owned_server(conn, data["serverId"], user.id)
        tid, now = secrets.token_urlsafe(12), now_iso()
        display_order = conn.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM tpm_tasks WHERE owner_user_id=?", (user.id,)).fetchone()[0]
        conn.execute("INSERT INTO tpm_tasks (id,owner_user_id,server_id,name,config_source,inline_yaml,remote_yaml_path,python_command,report_interval_seconds,enabled,show_in_tabs,display_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tid, user.id, data["serverId"], data["name"].strip(), data["configSource"], data.get("inlineYaml", ""), data.get("remoteYamlPath", "").strip(), _validate_python_command(data.get("pythonCommand", "python3")), int(data["reportIntervalSeconds"]), int(data.get("enabled", True)), int(data.get("showInTabs", True)), display_order, now, now))
        conn.commit(); return _task_public(conn.execute("SELECT * FROM tpm_tasks WHERE id=?", (tid,)).fetchone())


def _validate_task_input(data: dict[str, Any]) -> None:
    if not data.get("name", "").strip(): raise ToolboxError("INVALID_TASK", "任务名称不能为空", status_code=400, tool_id=TOOL_ID)
    if data.get("configSource") not in ("inline", "remote_yaml"): raise ToolboxError("INVALID_CONFIG_SOURCE", "配置来源无效", status_code=400, tool_id=TOOL_ID)
    if data.get("configSource") == "inline": _parse_config(data.get("inlineYaml", ""))
    elif not data.get("remoteYamlPath", "").strip(): raise ToolboxError("INVALID_CONFIG_PATH", "远程 YAML 路径不能为空", status_code=400, tool_id=TOOL_ID)


def update_task(task_id: str, data: dict[str, Any], user: User) -> dict[str, Any]:
    init_database(user.id); _validate_task_input(data)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        task = _owned_task(conn, task_id, user.id); _owned_server(conn, data["serverId"], user.id)
        yaml_changed = (
            task["config_source"] != data["configSource"]
            or (data["configSource"] == "inline" and task["inline_yaml"] != data.get("inlineYaml", ""))
            or (data["configSource"] == "remote_yaml" and task["remote_yaml_path"] != data.get("remoteYamlPath", "").strip())
        )
        conn.execute("UPDATE tpm_tasks SET server_id=?,name=?,config_source=?,inline_yaml=?,remote_yaml_path=?,python_command=?,report_interval_seconds=?,enabled=?,show_in_tabs=?,updated_at=? WHERE id=?", (data["serverId"], data["name"].strip(), data["configSource"], data.get("inlineYaml", ""), data.get("remoteYamlPath", "").strip(), _validate_python_command(data.get("pythonCommand", "python3")), int(data["reportIntervalSeconds"]), int(data.get("enabled", True)), int(data.get("showInTabs", True)), now_iso(), task_id))
        if yaml_changed:
            inline_hash = _yaml_hash(data.get("inlineYaml", "")) if data["configSource"] == "inline" else ""
            _invalidate_task_history(conn, task_id, inline_hash)
        conn.commit(); return _task_public(conn.execute("SELECT * FROM tpm_tasks WHERE id=?", (task_id,)).fetchone())


def delete_task(task_id: str, user: User) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        _owned_task(conn, task_id, user.id); conn.execute("DELETE FROM tpm_event_files WHERE task_id=?", (task_id,)); conn.execute("DELETE FROM tpm_run_samples WHERE task_id=?", (task_id,)); conn.execute("DELETE FROM tpm_reports WHERE task_id=?", (task_id,)); conn.execute("DELETE FROM tpm_tasks WHERE id=?", (task_id,)); conn.commit()


def copy_task(task_id: str, user: User) -> dict[str, Any]:
    """Duplicate report settings only; historical observations remain with the source report."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        source = _owned_task(conn, task_id, user.id)
        copied_id, now = secrets.token_urlsafe(12), now_iso()
        display_order = conn.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM tpm_tasks WHERE owner_user_id=?", (user.id,)).fetchone()[0]
        conn.execute(
            """INSERT INTO tpm_tasks (id,owner_user_id,server_id,name,config_source,inline_yaml,remote_yaml_path,python_command,report_interval_seconds,enabled,show_in_tabs,display_order,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (copied_id, user.id, source["server_id"], f'{source["name"]} 副本', source["config_source"], source["inline_yaml"], source["remote_yaml_path"], source["python_command"], source["report_interval_seconds"], source["enabled"], source["show_in_tabs"], display_order, now, now),
        )
        conn.commit()
        return _task_public(conn.execute("SELECT * FROM tpm_tasks WHERE id=?", (copied_id,)).fetchone())


def move_task(task_id: str, direction: str, user: User) -> dict[str, Any]:
    """Persist the order used by the report sub-navigation, separately for hidden reports."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        task = _owned_task(conn, task_id, user.id)
        rows = conn.execute(
            "SELECT * FROM tpm_tasks WHERE owner_user_id=? AND show_in_tabs=? ORDER BY display_order ASC, created_at ASC, id ASC",
            (user.id, task["show_in_tabs"]),
        ).fetchall()
        index = next(index for index, row in enumerate(rows) if row["id"] == task_id)
        target = index - 1 if direction == "up" else index + 1
        if 0 <= target < len(rows):
            rows[index], rows[target] = rows[target], rows[index]
        for order, row in enumerate(rows):
            conn.execute("UPDATE tpm_tasks SET display_order=?,updated_at=? WHERE id=?", (order, now_iso(), row["id"]))
        conn.commit()
        return _task_public(conn.execute("SELECT * FROM tpm_tasks WHERE id=?", (task_id,)).fetchone())


def list_tasks(user: User) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        return [_task_public(r) for r in conn.execute("SELECT * FROM tpm_tasks WHERE owner_user_id=? ORDER BY show_in_tabs DESC, display_order ASC, created_at ASC, id ASC", (user.id,)).fetchall()]


def _parse_config(raw: str) -> dict[str, Any]:
    try: data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc: raise ToolboxError("INVALID_YAML", f"YAML 无法解析: {exc}", status_code=400, tool_id=TOOL_ID) from exc
    if not isinstance(data, dict): raise ToolboxError("INVALID_CONFIG", "YAML 根节点必须是对象", status_code=400, tool_id=TOOL_ID)
    root, tag = str(data.get("tensorboard_root", "")).strip(), str(data.get("progress_tag", "")).strip()
    if not root or not tag: raise ToolboxError("INVALID_CONFIG", "必须设置 tensorboard_root 和 progress_tag", status_code=400, tool_id=TOOL_ID)
    mode = data.get("progress_mode", "event_step")
    if mode not in ("event_step", "scalar_value"): raise ToolboxError("INVALID_CONFIG", "progress_mode 必须为 event_step 或 scalar_value", status_code=400, tool_id=TOOL_ID)
    def number(key: str, default: int, lo: int, hi: int) -> int:
        try: value = int(data.get(key, default))
        except (ValueError, TypeError): raise ToolboxError("INVALID_CONFIG", f"{key} 必须是整数", status_code=400, tool_id=TOOL_ID)
        if not lo <= value <= hi: raise ToolboxError("INVALID_CONFIG", f"{key} 必须在 {lo} 到 {hi} 之间", status_code=400, tool_id=TOOL_ID)
        return value
    groups_raw = data.get("groups", [])
    if not isinstance(groups_raw, list): raise ToolboxError("INVALID_CONFIG", "groups 必须是列表", status_code=400, tool_id=TOOL_ID)

    def parse_groups(items: list[Any], parent_key: str = "") -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        sibling_names: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip() or not str(item.get("pattern", "")).strip(): raise ToolboxError("INVALID_CONFIG", "每个分类都需要 name 和 pattern", status_code=400, tool_id=TOOL_ID)
            name = str(item["name"]).strip()
            if name in sibling_names: raise ToolboxError("INVALID_CONFIG", f"同级分类名称重复: {name}", status_code=400, tool_id=TOOL_ID)
            sibling_names.add(name)
            try: target = float(item["target_step"])
            except (KeyError, ValueError, TypeError): raise ToolboxError("INVALID_CONFIG", f"分类 {name} 的 target_step 无效", status_code=400, tool_id=TOOL_ID)
            if target <= 0: raise ToolboxError("INVALID_CONFIG", "target_step 必须大于 0", status_code=400, tool_id=TOOL_ID)
            total = item.get("total_runs")
            if total is not None:
                try: total = int(total)
                except (ValueError, TypeError): raise ToolboxError("INVALID_CONFIG", "total_runs 必须是整数或空", status_code=400, tool_id=TOOL_ID)
                if total < 0: raise ToolboxError("INVALID_CONFIG", "total_runs 不能为负数", status_code=400, tool_id=TOOL_ID)
            children_raw = item.get("children", [])
            if not isinstance(children_raw, list): raise ToolboxError("INVALID_CONFIG", f"分类 {name} 的 children 必须是列表", status_code=400, tool_id=TOOL_ID)
            key = f"{parent_key}/{name}" if parent_key else name
            parsed.append({"name": name, "key": key, "pattern": str(item["pattern"]).strip(), "target_step": target, "total_runs": total, "children": parse_groups(children_raw, key)})
        return parsed

    groups = parse_groups(groups_raw)
    return {"tensorboard_root": root, "progress_tag": tag, "progress_mode": mode, "tail_bytes": number("tail_bytes", 1048576, 4096, 67108864), "report_interval_seconds": number("report_interval_seconds", 60, 30, 3600), "rate_report_count": number("rate_report_count", 5, 2, 20), "stale_after_seconds": number("stale_after_seconds", max(number("report_interval_seconds", 60, 30, 3600) * 3, 180), 30, 86400), "overall_concurrency": number("overall_concurrency", 1, 1, 128), "groups": groups}


def _ssh_spec(server: sqlite3.Row, timeout: int = 30) -> SSHConnectionSpec:
    encrypted = server["ssh_password_encrypted"]
    return SSHConnectionSpec(tool_id=TOOL_ID, server_id=server["id"], host=server["host"], port=int(server["port"]), username=server["ssh_username"], auth_fingerprint=ssh_connection_service.auth_fingerprint(encrypted), password=decrypt_secret(encrypted), connect_timeout=timeout, connect_error_code="SSH_CONNECT_FAILED", missing_dependency_code="SSH_DEPENDENCY_MISSING", missing_dependency_message="缺少 paramiko 依赖，无法执行 SSH 采集")


def test_server(server_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn: server = _owned_server(conn, server_id, user.id)
    out, err, code = ssh_connection_service.exec_command(_ssh_spec(server, 10), "python3 --version", timeout=10)
    return {"connected": code == 0, "python": out.strip() or err.strip(), "error": "" if code == 0 else (err.strip() or out.strip())}


def _read_remote_yaml(server: sqlite3.Row, path: str) -> str:
    with ssh_connection_service.borrowed_client(_ssh_spec(server)) as client:
        sftp = client.open_sftp()
        try:
            stat = sftp.stat(path)
            if stat.st_size > MAX_YAML_BYTES: raise ToolboxError("YAML_TOO_LARGE", "远程 YAML 超过 1 MiB", status_code=400, tool_id=TOOL_ID)
            with sftp.open(path, "rb") as fh: return fh.read(MAX_YAML_BYTES + 1).decode("utf-8")
        finally: sftp.close()


def _task_yaml_raw(task: sqlite3.Row, server: sqlite3.Row) -> str:
    return task["inline_yaml"] if task["config_source"] == "inline" else _read_remote_yaml(server, task["remote_yaml_path"])


def _yaml_hash(raw: str) -> str:
    """Fingerprint raw YAML so comments/formatting changes invalidate reports too."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_task_config(task: sqlite3.Row, server: sqlite3.Row) -> tuple[dict[str, Any], str]:
    raw = _task_yaml_raw(task, server)
    return _parse_config(raw), raw


def validate_task_config(task_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        task = _owned_task(conn, task_id, user.id); server = _owned_server(conn, task["server_id"], user.id)
    config, _ = _load_task_config(task, server)
    return {"valid": True, "config": config}


def _previous_file_states(conn: sqlite3.Connection, task_id: str) -> dict[str, dict[str, int]]:
    rows = conn.execute("SELECT path,size,mtime_ns FROM tpm_event_files WHERE task_id=?", (task_id,)).fetchall()
    return {str(row["path"]): {"size": int(row["size"]), "mtime_ns": int(row["mtime_ns"])} for row in rows}


def _same_collection_config(previous_json: str, config: dict[str, Any]) -> bool:
    """File metadata is only reusable when it was collected for the same bytes/tag semantics."""
    try:
        previous = json.loads(previous_json)
    except (TypeError, json.JSONDecodeError):
        return False
    keys = ("tensorboard_root", "progress_tag", "progress_mode", "tail_bytes")
    return all(previous.get(key) == config.get(key) for key in keys)


def _invalidate_task_history(conn: sqlite3.Connection, task_id: str, yaml_hash: str) -> None:
    """Discard samples, reports, and file cache whenever the raw YAML changes."""
    conn.execute("DELETE FROM tpm_event_files WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tpm_run_samples WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tpm_reports WHERE task_id=?", (task_id,))
    conn.execute("UPDATE tpm_tasks SET last_report_at=NULL,last_config_json='',last_yaml_hash=?,last_config_error='',updated_at=? WHERE id=?", (yaml_hash, now_iso(), task_id))


def _trim_report_history(conn: sqlite3.Connection, task_id: str, rate_report_count: int) -> None:
    """Keep at most twice the history needed by the rolling rate estimator."""
    keep = max(2, int(rate_report_count) * 2)
    rows = conn.execute("SELECT id FROM tpm_reports WHERE task_id=? ORDER BY reported_at DESC, id DESC", (task_id,)).fetchall()
    stale_ids = [str(row["id"]) for row in rows[keep:]]
    if not stale_ids:
        return
    placeholders = ",".join("?" for _ in stale_ids)
    conn.execute(f"DELETE FROM tpm_run_samples WHERE task_id=? AND report_id IN ({placeholders})", [task_id, *stale_ids])
    conn.execute(f"DELETE FROM tpm_reports WHERE task_id=? AND id IN ({placeholders})", [task_id, *stale_ids])


def _run_remote_collector(server: sqlite3.Row, python_command: str, config: dict[str, Any], previous_files: dict[str, dict[str, int]]) -> dict[str, Any]:
    request = base64.b64encode(json.dumps({"root": config["tensorboard_root"], "progress_tag": config["progress_tag"], "progress_mode": config["progress_mode"], "tail_bytes": config["tail_bytes"], "previous_files": previous_files}, separators=(",", ":")).encode()).decode()
    command = f"{_validate_python_command(python_command)} - --request-base64 {request}"
    with ssh_connection_service.borrowed_client(_ssh_spec(server, 60)) as client:
        stdin, stdout, stderr = client.exec_command(command, timeout=120)
        stdin.write(REMOTE_COLLECTOR_SOURCE); stdin.flush(); stdin.close()
        out = stdout.read().decode("utf-8", "replace").strip(); err = stderr.read().decode("utf-8", "replace").strip(); code = stdout.channel.recv_exit_status()
    try: result = json.loads(out)
    except json.JSONDecodeError as exc: raise ToolboxError("REMOTE_COLLECTOR_FAILED", f"远程采集器未返回 JSON: {(err or out)[:300]}", status_code=502, tool_id=TOOL_ID) from exc
    if code != 0 or result.get("error"): raise ToolboxError("REMOTE_COLLECTOR_FAILED", str(result.get("error") or err or "远程采集失败")[:500], status_code=502, tool_id=TOOL_ID)
    return result


def _get_task_lock(user_id: str, task_id: str) -> threading.Lock:
    with _task_locks_guard: return _task_locks.setdefault((user_id, task_id), threading.Lock())


def _match_group(path: str, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the deepest matching group; child patterns only run inside a parent match."""
    for group in groups:
        if not fnmatch.fnmatchcase(path, group["pattern"]):
            continue
        child = _match_group(path, group["children"])
        return child or group
    return None


def _flatten_groups(groups: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for group in groups:
        flattened.append({**group, "depth": depth})
        flattened.extend(_flatten_groups(group["children"], depth + 1))
    return flattened


def _merge_cached_progress(conn: sqlite3.Connection, task_id: str, remote: dict[str, Any]) -> None:
    """Keep the last known tag value for runs whose files were not read this cycle."""
    rows = conn.execute("""
        SELECT s.run_key,s.progress,s.event_time,s.started_at,s.file_mtime
        FROM tpm_run_samples s JOIN tpm_reports r ON r.id=s.report_id
        WHERE s.task_id=? AND r.success=1
        ORDER BY r.reported_at DESC
    """, (task_id,)).fetchall()
    cached: dict[str, sqlite3.Row] = {}
    for row in rows:
        cached.setdefault(str(row["run_key"]), row)
    for raw in remote.get("runs", []):
        if raw.get("latest") is not None:
            continue
        prior = cached.get(str(raw.get("relative_path", "")))
        if prior is None or prior["progress"] is None:
            continue
        raw["latest"] = {"progress": float(prior["progress"]), "event_time": prior["event_time"]}
        if raw.get("start_hint") is None:
            raw["start_hint"] = prior["started_at"]
        if raw.get("last_file_mtime") is None:
            raw["last_file_mtime"] = prior["file_mtime"]


def _prior_samples(conn: sqlite3.Connection, task_id: str, key: str, count: int) -> list[tuple[float, float]]:
    rows = conn.execute("SELECT r.reported_at,s.progress FROM tpm_run_samples s JOIN tpm_reports r ON r.id=s.report_id WHERE s.task_id=? AND s.run_key=? AND r.success=1 AND s.progress IS NOT NULL ORDER BY r.reported_at DESC LIMIT ?", (task_id, key, count - 1)).fetchall()
    return [(datetime.fromisoformat(r["reported_at"]).timestamp(), float(r["progress"])) for r in reversed(rows)]


def _completed_durations(conn: sqlite3.Connection, task_id: str) -> dict[str, list[float]]:
    rows = conn.execute("""SELECT s.group_name,s.run_key,s.duration_seconds FROM tpm_run_samples s JOIN tpm_reports r ON r.id=s.report_id
      WHERE s.task_id=? AND s.status='completed' AND s.duration_seconds IS NOT NULL AND r.success=1
      AND r.reported_at=(SELECT MAX(r2.reported_at) FROM tpm_run_samples s2 JOIN tpm_reports r2 ON r2.id=s2.report_id WHERE s2.task_id=s.task_id AND s2.run_key=s.run_key AND s2.status='completed' AND r2.success=1)""", (task_id,)).fetchall()
    values: dict[str, list[float]] = {}
    for row in rows:
        if row["group_name"]: values.setdefault(row["group_name"], []).append(float(row["duration_seconds"]))
    return values


def _make_report(conn: sqlite3.Connection, task: sqlite3.Row, server: sqlite3.Row, config: dict[str, Any], remote: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = time.time(); runs: list[dict[str, Any]] = []
    duration_history = _completed_durations(conn, task["id"])
    for raw in remote.get("runs", []):
        path = str(raw.get("relative_path", "")); group = _match_group(path, config["groups"]); latest = raw.get("latest")
        key = path
        target = group["target_step"] if group else None
        progress = latest.get("progress") if isinstance(latest, dict) else None
        event_time = latest.get("event_time") if isinstance(latest, dict) else None
        started = raw.get("start_hint")
        file_mtime = raw.get("last_file_mtime")
        status, rate, eta, duration, error, estimate_as_queued = "unmatched", None, None, None, "", False
        if group:
            stale = file_mtime is not None and now - float(file_mtime) > config["stale_after_seconds"]
            if progress is None:
                status, error = ("stalled", "日志文件超过停滞阈值未修改，且未找到指定进度 tag") if stale else ("waiting", "未在尾部窗口找到指定进度 tag")
                estimate_as_queued = True
            elif progress >= target:
                status = "completed"; duration = max(0.0, float(event_time) - float(started)) if started and event_time else None
            elif stale:
                status, error = "stalled", "日志文件超过停滞阈值未修改"
                estimate_as_queued = True
            else:
                status = "running"
                points = _prior_samples(conn, task["id"], key, config["rate_report_count"]) + [(now, float(progress))]
                if len(points) >= 2:
                    elapsed = points[-1][0] - points[0][0]; delta = points[-1][1] - points[0][1]
                    if elapsed > 0 and delta > 0: rate, eta = delta / elapsed, max(0.0, (target - float(progress)) / (delta / elapsed))
                    else: error = "近期报表没有正向步数增长"
                else:
                    error = "历史报表不足，暂不能计算速率"
                    # A newly discovered run has not supplied enough progress
                    # samples yet.  Its remaining time is therefore modelled as
                    # one not-yet-started job, rather than invalidating all ETA.
                    estimate_as_queued = True
        runs.append({"runKey": key, "relativePath": path, "groupName": group["key"] if group else None, "progress": progress, "targetStep": target, "eventTime": event_time, "startedAt": started, "fileMtime": file_mtime, "status": status, "ratePerSecond": rate, "etaSeconds": eta, "durationSeconds": duration, "error": error, "estimateAsQueued": estimate_as_queued, "eventFileCount": raw.get("event_file_count", 0)})
        if status == "completed" and duration is not None and group: duration_history.setdefault(group["key"], []).append(duration)

    def summarize_group(group: dict[str, Any], depth: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        children: list[dict[str, Any]] = []
        flattened: list[dict[str, Any]] = []
        for child in group["children"]:
            child_summary, child_flattened = summarize_group(child, depth + 1)
            children.append(child_summary); flattened.extend(child_flattened)
        direct = [run for run in runs if run["groupName"] == group["key"]]
        direct_completed = sum(run["status"] == "completed" for run in direct)
        direct_active = [run for run in direct if run["status"] in ("running", "stalled", "waiting")]
        provisional_active = [run for run in direct_active if run["estimateAsQueued"]]
        unestimated_active = [run for run in direct_active if run["etaSeconds"] is None and not run["estimateAsQueued"]]
        child_totals = [child["effectiveTotalRuns"] for child in children]
        known_child_total = None if any(total is None for total in child_totals) else sum(int(total) for total in child_totals)
        configured_total = group["total_runs"]
        warning = ""
        if configured_total is None:
            effective_total = None
        elif known_child_total is not None and configured_total < known_child_total:
            effective_total = known_child_total
            warning = f"父群 total_runs={configured_total} 小于子群总数 {known_child_total}，已按子群总数计算"
        else:
            effective_total = configured_total
        completed_durations = duration_history.get(group["key"], [])
        # Prefer observed completed durations.  Before the first completion,
        # an active run with a measured rate still gives a useful estimate of
        # its full one-run duration: elapsed wall time plus remaining time.
        running_duration_estimates = [
            float(run["etaSeconds"]) + max(0.0, now - float(run["startedAt"])) if run["startedAt"] is not None else float(run["etaSeconds"])
            for run in direct
            if run["status"] == "running" and run["etaSeconds"] is not None
        ]
        if completed_durations:
            median, duration_source = statistics.median(completed_durations), "completed"
        elif running_duration_estimates:
            median, duration_source = statistics.median(running_duration_estimates), "running_estimate"
        else:
            median, duration_source = None, "unavailable"
        own_total = None if effective_total is None or known_child_total is None else effective_total - known_child_total
        own_eta, own_reason = None, ""
        direct_queued = None
        if own_total is None:
            own_reason = "未配置 total_runs，或子群总数未知"
        elif direct_completed + len(direct_active) > own_total:
            own_reason = "直属实验数量超过可分配的父群数量"
        elif unestimated_active:
            own_reason = "存在无法估算的直属进行中实验"
        else:
            direct_queued = max(0, own_total - direct_completed - len(direct_active))
            estimate_queued = direct_queued + len(provisional_active)
            if estimate_queued and median is None:
                own_reason = "没有直属已完成或可估算运行实验可提供单次时长"
            else:
                own_eta = sum(float(run["etaSeconds"]) for run in direct_active if run["etaSeconds"] is not None) + estimate_queued * (median or 0.0)
        child_eta_known = all(child["etaSeconds"] is not None for child in children)
        eta = own_eta + sum(float(child["etaSeconds"]) for child in children) if own_eta is not None and child_eta_known else None
        reasons = [text for text in (warning, own_reason, *[child["reason"] for child in children if child["etaSeconds"] is None]) if text]
        summary = {
            "name": group["key"], "shortName": group["name"], "depth": depth, "pattern": group["pattern"], "targetStep": group["target_step"],
            "configuredTotalRuns": configured_total, "effectiveTotalRuns": effective_total, "totalRuns": effective_total,
            "completedRuns": direct_completed + sum(child["completedRuns"] for child in children),
            "activeRuns": len(direct_active) + sum(child["activeRuns"] for child in children),
            "queuedRuns": None if own_total is None or any(child["queuedRuns"] is None for child in children) else max(0, own_total - direct_completed - len(direct_active)) + sum(int(child["queuedRuns"]) for child in children),
            "directQueuedRuns": direct_queued,
            "estimateQueuedRuns": None if direct_queued is None else direct_queued + len(provisional_active),
            "provisionalQueuedRuns": len(provisional_active),
            "medianDurationSeconds": median, "durationSource": duration_source, "etaSeconds": eta, "reason": "；".join(dict.fromkeys(reasons)),
        }
        return summary, [summary, *flattened]

    root_summaries: list[dict[str, Any]] = []
    group_summaries: list[dict[str, Any]] = []
    for root in config["groups"]:
        root_summary, flattened = summarize_group(root, 0)
        root_summaries.append(root_summary); group_summaries.extend(flattened)
    # Overall ETA models actual experiment slots rather than treating each root
    # category as one serial, indivisible job.  Running runs seed the slots with
    # their individual remaining time; each not-yet-started direct run is then
    # greedily assigned its group's historical median duration.
    overall_jobs: list[float] = []
    overall_reason = ""
    unknown_running = [run for run in runs if run["status"] in ("running", "stalled", "waiting") and run["etaSeconds"] is None and not run["estimateAsQueued"]]
    if not group_summaries: overall_eta, overall_reason = None, "尚未配置分类"
    elif unknown_running: overall_eta, overall_reason = None, "存在无法估算的进行中实验"
    else:
        overall_jobs.extend(float(run["etaSeconds"]) for run in runs if run["status"] == "running" and run["etaSeconds"] is not None)
        unknown_queued = [group for group in group_summaries if group["estimateQueuedRuns"] is None or (group["estimateQueuedRuns"] > 0 and group["medianDurationSeconds"] is None)]
        if unknown_queued:
            overall_eta, overall_reason = None, "存在没有历史时长的未开始实验，无法估算整体 ETA"
        else:
            for group in group_summaries:
                queued = int(group["estimateQueuedRuns"])
                if queued:
                    overall_jobs.extend([float(group["medianDurationSeconds"])] * queued)
        if overall_reason:
            pass
        elif not overall_jobs:
            overall_eta = 0.0
        else:
            slots = [0.0] * config["overall_concurrency"]
            for job in sorted(overall_jobs, reverse=True): slots[slots.index(min(slots))] += job
            overall_eta = max(slots)
    summary = {"generatedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(), "config": {k: config[k] for k in ("tensorboard_root", "progress_tag", "progress_mode", "tail_bytes", "report_interval_seconds", "rate_report_count", "stale_after_seconds", "overall_concurrency")}, "counts": {key: sum(r["status"] == key for r in runs) for key in ("running", "completed", "stalled", "waiting", "unmatched")}, "groups": group_summaries, "overallEtaSeconds": overall_eta, "overallEtaReason": overall_reason, "overallEtaMethod": "按单实验剩余时间和分类单次时长进行并发槽位模拟", "remoteErrors": remote.get("errors", []), "collector": {"readFileCount": remote.get("read_file_count", 0), "skippedFileCount": remote.get("skipped_file_count", 0)}}
    return summary, runs


def _save_report(conn: sqlite3.Connection, task: sqlite3.Row, summary: dict[str, Any], runs: list[dict[str, Any]], config: dict[str, Any], remote: dict[str, Any] | None = None, error: str = "", yaml_hash: str = "") -> dict[str, Any]:
    rid, reported = secrets.token_urlsafe(12), now_iso(); success = not error
    conn.execute("INSERT INTO tpm_reports VALUES (?,?,?,?,?,?,?)", (rid, task["id"], reported, int(success), yaml_hash if success else "", json.dumps(summary, ensure_ascii=False), error))
    if success:
        for item in runs:
            conn.execute("""INSERT INTO tpm_run_samples
                (id,report_id,task_id,reported_at,run_key,relative_path,group_name,progress,target_step,event_time,started_at,status,rate_per_second,eta_seconds,duration_seconds,error,file_mtime)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (secrets.token_urlsafe(12), rid, task["id"], reported, item["runKey"], item["relativePath"], item["groupName"], item["progress"], item["targetStep"], item["eventTime"], item["startedAt"], item["status"], item["ratePerSecond"], item["etaSeconds"], item["durationSeconds"], item["error"], item["fileMtime"]))
        files = (remote or {}).get("files", [])
        if files:
            paths = [str(item["path"]) for item in files]
            conn.execute(f"DELETE FROM tpm_event_files WHERE task_id=? AND path NOT IN ({','.join('?' for _ in paths)})", [task["id"], *paths])
            for item in files:
                conn.execute("""INSERT INTO tpm_event_files (task_id,path,size,mtime_ns,last_seen_at) VALUES (?,?,?,?,?)
                    ON CONFLICT(task_id,path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,last_seen_at=excluded.last_seen_at""", (task["id"], str(item["path"]), int(item["size"]), int(item["mtime_ns"]), reported))
        else:
            conn.execute("DELETE FROM tpm_event_files WHERE task_id=?", (task["id"],))
        conn.execute("UPDATE tpm_tasks SET last_report_at=?,report_interval_seconds=?,last_config_json=?,last_yaml_hash=?,last_config_error='',updated_at=? WHERE id=?", (reported, config["report_interval_seconds"], json.dumps(config, ensure_ascii=False), yaml_hash, reported, task["id"]))
    else: conn.execute("UPDATE tpm_tasks SET last_config_error=?,updated_at=? WHERE id=?", (error, reported, task["id"]))
    _trim_report_history(conn, task["id"], int(config.get("rate_report_count", 5)))
    conn.commit(); return {"reportId": rid, "success": success, "summary": summary, "runs": runs, "error": error, "reportedAt": reported}


def _run_task(user_id: str, task_id: str) -> dict[str, Any]:
    lock = _get_task_lock(user_id, task_id)
    if not lock.acquire(blocking=False): raise ToolboxError("REFRESH_IN_PROGRESS", "该任务正在生成报表", status_code=409, tool_id=TOOL_ID)
    try:
        init_database(user_id)
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            task = conn.execute("SELECT * FROM tpm_tasks WHERE id=? AND owner_user_id=?", (task_id, user_id)).fetchone()
            if not task: raise ToolboxError("TASK_NOT_FOUND", "监控任务不存在", status_code=404, tool_id=TOOL_ID)
            server = _owned_server(conn, task["server_id"], user_id)
            try:
                raw_yaml = _task_yaml_raw(task, server)
                yaml_hash = _yaml_hash(raw_yaml)
                if yaml_hash != task["last_yaml_hash"]:
                    _invalidate_task_history(conn, task["id"], yaml_hash)
                    task = conn.execute("SELECT * FROM tpm_tasks WHERE id=?", (task["id"],)).fetchone()
                config = _parse_config(raw_yaml)
                previous_files = _previous_file_states(conn, task["id"]) if _same_collection_config(task["last_config_json"], config) else {}
                remote = _run_remote_collector(server, task["python_command"], config, previous_files)
                _merge_cached_progress(conn, task["id"], remote); summary, runs = _make_report(conn, task, server, config, remote); return _save_report(conn, task, summary, runs, config, remote, yaml_hash=yaml_hash)
            except Exception as exc:
                message = exc.message if isinstance(exc, ToolboxError) else str(exc)
                return _save_report(conn, task, {"generatedAt": now_iso(), "counts": {}, "groups": [], "overallEtaSeconds": None, "overallEtaReason": message, "remoteErrors": []}, [], {}, error=message[:500])
    finally: lock.release()


def refresh_task(task_id: str, user: User) -> dict[str, Any]: return _run_task(user.id, task_id)


def _report_public(conn: sqlite3.Connection, report: sqlite3.Row) -> dict[str, Any]:
    runs = [dict(r) for r in conn.execute("SELECT run_key AS runKey,relative_path AS relativePath,group_name AS groupName,progress,target_step AS targetStep,event_time AS eventTime,started_at AS startedAt,file_mtime AS fileMtime,status,rate_per_second AS ratePerSecond,eta_seconds AS etaSeconds,duration_seconds AS durationSeconds,error FROM tpm_run_samples WHERE report_id=? ORDER BY group_name,relative_path", (report["id"],)).fetchall()]
    return {"reportId": report["id"], "reportedAt": report["reported_at"], "success": bool(report["success"]), "summary": json.loads(report["summary_json"]), "error": report["error"], "runs": runs}


def get_latest_report(task_id: str, user: User) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        _owned_task(conn, task_id, user.id); report = conn.execute("SELECT * FROM tpm_reports WHERE task_id=? ORDER BY reported_at DESC LIMIT 1", (task_id,)).fetchone()
        return {"report": _report_public(conn, report) if report else None}


def get_history(task_id: str, user: User, limit: int = 30) -> dict[str, Any]:
    init_database(user.id); limit = max(1, min(200, limit))
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        _owned_task(conn, task_id, user.id); rows = conn.execute("SELECT * FROM tpm_reports WHERE task_id=? ORDER BY reported_at DESC LIMIT ?", (task_id, limit)).fetchall()
        return {"reports": [{"reportId": r["id"], "reportedAt": r["reported_at"], "success": bool(r["success"]), "summary": json.loads(r["summary_json"]), "error": r["error"]} for r in rows]}


def _due(row: sqlite3.Row, now: datetime) -> bool:
    if not row["last_report_at"]: return True
    try: last = datetime.fromisoformat(row["last_report_at"])
    except ValueError: return True
    if last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
    return last + timedelta(seconds=max(30, int(row["report_interval_seconds"]))) <= now


def collect_due_reports() -> None:
    now = datetime.now(timezone.utc)
    for user_id, _ in list_user_tool_dbs(TOOL_ID):
        init_database(user_id)
        with user_tool_connection_context(user_id, TOOL_ID) as conn: tasks = conn.execute("SELECT id,last_report_at,report_interval_seconds FROM tpm_tasks WHERE enabled=1").fetchall()
        for task in tasks:
            if _due(task, now):
                try: _run_task(user_id, task["id"])
                except Exception: logger.exception("Failed scheduled TensorBoard progress report: user=%s task=%s", user_id, task["id"])
