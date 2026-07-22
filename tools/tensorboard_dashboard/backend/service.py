"""TensorBoard dashboard service: server CRUD, session management, SSH tunnels."""
from __future__ import annotations

import base64
import io
import logging
import secrets
import shlex
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import user_tool_connection_context
from backend.app.services import ssh_connection_service
from backend.app.services.data_management import DataCategory, register_tool_categories
from backend.app.services.ssh_connection_service import SSHConnectionSpec

logger = logging.getLogger(__name__)

TOOL_ID = "tensorboard_dashboard"

# Port range for remote TensorBoard instances.
REMOTE_PORT_START = 6006
REMOTE_PORT_END = 6099

_initialized_dbs: set[str] = set()


# ============================================================
# Database
# ============================================================


def init_database(user_id: str) -> None:
    if user_id in _initialized_dbs:
        return
    with user_tool_connection_context(user_id, TOOL_ID) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tb_servers (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                ssh_username TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'password',
                ssh_password_encrypted TEXT NOT NULL DEFAULT '',
                private_key_encrypted TEXT NOT NULL DEFAULT '',
                private_key_passphrase_encrypted TEXT NOT NULL DEFAULT '',
                conda_base_path TEXT NOT NULL DEFAULT '',
                last_test_status TEXT NOT NULL DEFAULT 'unknown',
                last_test_error TEXT NOT NULL DEFAULT '',
                last_tested_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tb_servers_owner
                ON tb_servers(owner_user_id, enabled);

            CREATE TABLE IF NOT EXISTS tb_sessions (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                server_id TEXT NOT NULL,
                name TEXT NOT NULL,
                logdir TEXT NOT NULL,
                remote_port INTEGER NOT NULL,
                local_port INTEGER NOT NULL,
                python_mode TEXT NOT NULL DEFAULT 'conda',
                conda_env TEXT NOT NULL DEFAULT '',
                python_path TEXT NOT NULL DEFAULT '',
                extra_params TEXT NOT NULL DEFAULT '',
                remote_pid TEXT NOT NULL DEFAULT '',
                tb_session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'starting',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(server_id) REFERENCES tb_servers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_tb_sessions_owner
                ON tb_sessions(owner_user_id, status);
            """
        )
        # Migration: add conda_base_path to tb_servers if missing
        try:
            conn.execute("SELECT conda_base_path FROM tb_servers LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tb_servers ADD COLUMN conda_base_path TEXT NOT NULL DEFAULT ''")
        # Migration: add updated_at to tb_sessions if missing
        try:
            conn.execute("SELECT updated_at FROM tb_sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tb_sessions ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        # Migration: add extra_params to tb_sessions if missing
        try:
            conn.execute("SELECT extra_params FROM tb_sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE tb_sessions ADD COLUMN extra_params TEXT NOT NULL DEFAULT ''")
    _initialized_dbs.add(user_id)


register_tool_categories(TOOL_ID, [
    DataCategory(
        name="config",
        tables=["tb_servers", "tb_sessions"],
        time_column=None,
        description="配置数据（服务器、TensorBoard 会话）",
        storage="user_tool_db",
    ),
])


# ============================================================
# SSH Tunnel Management
# ============================================================


@dataclass
class TunnelEntry:
    """An active SSH port-forward tunnel for a TensorBoard session."""
    session_id: str
    ssh_client: Any
    transport: Any
    local_port: int
    remote_port: int
    server_socket: socket.socket
    _running: bool = True
    _thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self.server_socket.close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        try:
            self.transport.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.ssh_client.close()
        except Exception:  # noqa: BLE001
            pass

    def is_alive(self) -> bool:
        try:
            return bool(self.transport.is_active())
        except Exception:  # noqa: BLE001
            return False

    def _accept_loop(self) -> None:
        while self._running:
            try:
                sock, _ = self.server_socket.accept()
            except OSError:
                break
            if not self._running:
                sock.close()
                break
            t = threading.Thread(target=self._handle_connection, args=(sock,), daemon=True)
            t.start()

    def _handle_connection(self, sock: socket.socket) -> None:
        chan = None
        try:
            chan = self.transport.open_channel(
                "direct-tcpip",
                ("127.0.0.1", self.remote_port),
                sock.getpeername(),
            )
            if chan is None:
                return
            _pipe_sockets(sock, chan)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                if chan is not None:
                    chan.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


_active_tunnels: dict[str, TunnelEntry] = {}
_tunnels_lock = threading.Lock()


def _pipe_sockets(sock: socket.socket, chan: Any) -> None:
    """Bidirectionally pipe data between a socket and a paramiko channel."""
    import select

    sock.setblocking(False)
    chan.setblocking(False)
    while True:
        try:
            r, _, _ = select.select([sock, chan], [], [], 60)
        except (OSError, ValueError):
            break
        if sock in r:
            try:
                data = sock.recv(65536)
            except BlockingIOError:
                data = None
            except (ConnectionError, OSError):
                break
            if data is None:
                pass
            elif not data:
                break
            else:
                try:
                    chan.sendall(data)
                except Exception:  # noqa: BLE001
                    break
        if chan in r:
            try:
                data = chan.recv(65536)
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            try:
                sock.sendall(data)
            except Exception:  # noqa: BLE001
                break


def _find_free_local_port() -> int:
    """Find a free local port by binding to port 0."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _find_free_remote_port(row: sqlite3.Row) -> int:
    """Find a free port on the remote server by checking a range."""
    spec = _ssh_spec(row)
    for port in range(REMOTE_PORT_START, REMOTE_PORT_END + 1):
        check_cmd = (
            f"python3 -c \"import socket; s=socket.socket(); "
            f"s.bind(('127.0.0.1', {port})); s.close()\" 2>/dev/null "
            f"&& echo FREE || echo TAKEN"
        )
        try:
            out, _, _ = ssh_connection_service.exec_command(spec, check_cmd, timeout=10)
            if "FREE" in out:
                return port
        except Exception:  # noqa: BLE001
            continue
    raise ToolboxError(
        "NO_FREE_PORT",
        f"远程服务器上 {REMOTE_PORT_START}-{REMOTE_PORT_END} 范围内无可用端口",
        status_code=502,
        tool_id=TOOL_ID,
    )


def _start_tunnel(row: sqlite3.Row, tb_session_id: str, remote_port: int) -> int:
    """Create a dedicated SSH connection and port forward. Returns local port."""
    local_port = _find_free_local_port()
    client = _ssh_connect_dedicated(row, timeout=20)
    transport = client.get_transport()
    if transport is None:
        client.close()
        raise ToolboxError("SSH_TRANSPORT_FAILED", "SSH 传输通道创建失败", status_code=502, tool_id=TOOL_ID)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", local_port))
        server_socket.listen(5)
    except OSError as exc:
        client.close()
        server_socket.close()
        raise ToolboxError("LOCAL_PORT_BIND_FAILED", f"本地端口绑定失败: {exc}", status_code=500, tool_id=TOOL_ID) from exc

    entry = TunnelEntry(
        session_id=tb_session_id,
        ssh_client=client,
        transport=transport,
        local_port=local_port,
        remote_port=remote_port,
        server_socket=server_socket,
    )
    entry.start()

    with _tunnels_lock:
        _active_tunnels[tb_session_id] = entry

    return local_port


def _stop_tunnel(tb_session_id: str) -> None:
    with _tunnels_lock:
        entry = _active_tunnels.pop(tb_session_id, None)
    if entry is not None:
        entry.stop()


def get_tunnel(tb_session_id: str) -> TunnelEntry | None:
    with _tunnels_lock:
        return _active_tunnels.get(tb_session_id)


def is_session_owner_by_tb_id(tb_session_id: str, user_id: str) -> bool:
    """Check if a session belongs to the user by tb_session_id (used by proxy)."""
    try:
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            row = conn.execute(
                "SELECT id FROM tb_sessions WHERE tb_session_id = ? AND owner_user_id = ?",
                (tb_session_id, user_id),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def is_session_owner(session_id: str, user_id: str) -> bool:
    """Check if a session belongs to the user (used by proxy middleware)."""
    try:
        with user_tool_connection_context(user_id, TOOL_ID) as conn:
            row = conn.execute(
                "SELECT id FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def close_all_tunnels() -> None:
    """Close all active tunnels (called on shutdown)."""
    with _tunnels_lock:
        entries = list(_active_tunnels.values())
        _active_tunnels.clear()
    for entry in entries:
        entry.stop()


# ============================================================
# Servers CRUD
# ============================================================


def list_servers(user: Any) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute(
            "SELECT * FROM tb_servers WHERE owner_user_id = ? AND enabled = 1 ORDER BY name ASC",
            (user.id,),
        ).fetchall()
    return [_public_server(row) for row in rows]


def create_server(payload: dict[str, Any], user: Any) -> dict[str, Any]:
    init_database(user.id)
    auth_type = _clean_auth_type(payload.get("authType", "password"))
    _validate_server_payload(payload, auth_type, creating=True)
    now = _now()
    server_id = _new_id()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            """
            INSERT INTO tb_servers (
                id, owner_user_id, name, host, port, ssh_username, auth_type,
                ssh_password_encrypted, private_key_encrypted, private_key_passphrase_encrypted,
                conda_base_path,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id, user.id,
                _required(payload, "name"), _required(payload, "host"),
                int(payload.get("port") or 22), _required(payload, "sshUsername"),
                auth_type,
                _encrypt(payload.get("sshPassword") or "") if payload.get("sshPassword") else "",
                _encrypt(payload.get("privateKey") or "") if payload.get("privateKey") else "",
                _encrypt(payload.get("privateKeyPassphrase") or "") if payload.get("privateKeyPassphrase") else "",
                (payload.get("condaBasePath") or "").strip(),
                now, now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tb_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def update_server(server_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    row = get_server(server_id, user)
    auth_type = _clean_auth_type(payload.get("authType", row["auth_type"]))
    _validate_server_payload(payload, auth_type, creating=False)
    password = payload.get("sshPassword")
    private_key = payload.get("privateKey")
    passphrase = payload.get("privateKeyPassphrase")
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            """
            UPDATE tb_servers
            SET name = ?, host = ?, port = ?, ssh_username = ?, auth_type = ?,
                ssh_password_encrypted = ?, private_key_encrypted = ?,
                private_key_passphrase_encrypted = ?, conda_base_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("name", row["name"]), payload.get("host", row["host"]),
                int(payload.get("port") or row["port"]),
                payload.get("sshUsername", row["ssh_username"]), auth_type,
                _encrypt(password) if password is not None and password != "" else row["ssh_password_encrypted"],
                _encrypt(private_key) if private_key is not None and private_key != "" else row["private_key_encrypted"],
                _encrypt(passphrase) if passphrase is not None and passphrase != "" else row["private_key_passphrase_encrypted"],
                (payload.get("condaBasePath") or "").strip() if "condaBasePath" in payload else (row["conda_base_path"] if "conda_base_path" in row.keys() else ""),
                _now(), server_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM tb_servers WHERE id = ?", (server_id,)).fetchone()
    ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)
    return _public_server(updated)


def delete_server(server_id: str, user: Any) -> None:
    get_server(server_id, user)
    # Stop any active sessions on this server.
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        sessions = conn.execute(
            "SELECT id FROM tb_sessions WHERE owner_user_id = ? AND server_id = ? AND status IN ('starting', 'running')",
            (user.id, server_id),
        ).fetchall()
    for s in sessions:
        try:
            stop_session(s["id"], user)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to stop session %s during server deletion", s["id"])
    now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("UPDATE tb_servers SET enabled = 0, updated_at = ? WHERE id = ?", (now, server_id))
        conn.commit()
    ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)


def get_server(server_id: str, user: Any) -> sqlite3.Row:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_servers WHERE id = ? AND owner_user_id = ? AND enabled = 1",
            (server_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    return row


def test_server(server_id: str, user: Any) -> dict[str, Any]:
    """Test SSH connectivity and Anaconda availability (if configured)."""
    row = get_server(server_id, user)
    now = _now()
    result: dict[str, Any] = {}

    # --- SSH check ---
    try:
        client = _ssh_connect(row, timeout=10)
        try:
            whoami_out, _, _ = _ssh_exec(client, "whoami", timeout=8)
        finally:
            client.close()
        result["ssh"] = {"connected": True, "user": whoami_out.strip()}
    except Exception as exc:  # noqa: BLE001
        ssh_connection_service.invalidate(tool_id=TOOL_ID, server_id=server_id)
        result["ssh"] = {"connected": False, "error": str(exc)[:500]}

    # --- Anaconda check (only if conda_base_path is configured) ---
    conda_base_path = row["conda_base_path"] if "conda_base_path" in row.keys() else ""
    if conda_base_path:
        conda_sh = shlex.quote(f"{conda_base_path.rstrip('/')}/etc/profile.d/conda.sh")
        cmd = f"source {conda_sh} 2>/dev/null && conda --version 2>&1 && echo CONDA_OK || echo CONDA_FAIL"
        try:
            out, _, _ = ssh_connection_service.exec_command(_ssh_spec(row), cmd, timeout=10)
            if "CONDA_OK" in out:
                version_line = [l for l in out.splitlines() if l.strip() and "CONDA" not in l]
                result["anaconda"] = {"ok": True, "version": version_line[0].strip() if version_line else "ok"}
            else:
                result["anaconda"] = {"ok": False, "error": "conda 命令不可用或路径不正确"}
        except Exception as exc:  # noqa: BLE001
            result["anaconda"] = {"ok": False, "error": str(exc)[:300]}

    # --- Overall status ---
    ssh_ok = result.get("ssh", {}).get("connected", False)
    conda_ok = result.get("anaconda", {}).get("ok", True) if conda_base_path else True
    status = "ok" if (ssh_ok and conda_ok) else "failed"
    error_parts: list[str] = []
    if not ssh_ok:
        error_parts.append(result["ssh"].get("error", "SSH 连接失败"))
    if conda_base_path and not conda_ok:
        error_parts.append(result["anaconda"].get("error", "Anaconda 检测失败"))
    error = "; ".join(error_parts)

    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            "UPDATE tb_servers SET last_test_status = ?, last_test_error = ?, last_tested_at = ?, updated_at = ? WHERE id = ?",
            (status, error, now, now, server_id),
        )
        conn.commit()
    return result


def check_python_env(server_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    """Check if the Python environment has Python and tensorboard installed."""
    row = get_server(server_id, user)
    python_mode = payload.get("pythonMode", "conda")
    conda_env = (payload.get("condaEnv") or "").strip()
    python_path = (payload.get("pythonPath") or "").strip()
    conda_base_path = row["conda_base_path"] if "conda_base_path" in row.keys() else ""

    if python_mode == "conda":
        if not conda_base_path:
            return {"ok": False, "error": "该服务器未配置 Anaconda 路径"}
        if not conda_env:
            return {"ok": False, "error": "请先选择 conda 环境"}
        conda_sh = shlex.quote(f"{conda_base_path.rstrip('/')}/etc/profile.d/conda.sh")
        check_cmd = (
            f"source {conda_sh} 2>/dev/null && conda activate {shlex.quote(conda_env)} 2>/dev/null && "
            f"python --version 2>&1 && "
            f"python -c 'import tensorboard; print(\"TB_OK\", tensorboard.__version__)' 2>&1"
        )
    else:
        if not python_path:
            return {"ok": False, "error": "请先填写 Python 路径"}
        py = shlex.quote(python_path)
        check_cmd = (
            f"{py} --version 2>&1 && "
            f"{py} -c 'import tensorboard; print(\"TB_OK\", tensorboard.__version__)' 2>&1"
        )

    try:
        out, _, _ = ssh_connection_service.exec_command(_ssh_spec(row), check_cmd, timeout=20)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        has_python = any("Python" in l for l in lines)
        has_tb = any("TB_OK" in l for l in lines)
        python_info = next((l for l in lines if "Python" in l), "")
        tb_info = next((l for l in lines if "TB_OK" in l), "").replace("TB_OK", "").strip()
        return {
            "ok": has_python and has_tb,
            "hasPython": has_python,
            "hasTensorboard": has_tb,
            "pythonVersion": python_info,
            "tensorboardVersion": tb_info,
            "raw": out[:500],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


def list_conda_envs(server_id: str, user: Any) -> dict[str, Any]:
    """List available conda environments on the remote server."""
    row = get_server(server_id, user)
    conda_base_path = row["conda_base_path"] if "conda_base_path" in row.keys() else ""
    if not conda_base_path:
        return {"envs": [], "error": "该服务器未配置 Anaconda 路径"}
    conda_sh = shlex.quote(f"{conda_base_path.rstrip('/')}/etc/profile.d/conda.sh")
    cmd = f"source {conda_sh} 2>/dev/null && conda env list --json 2>/dev/null"
    try:
        out, err, code = ssh_connection_service.exec_command(_ssh_spec(row), cmd, timeout=15)
        if code != 0:
            return {"envs": [], "error": (err or out)[:300]}
        import json as _json
        data = _json.loads(out)
        envs = [e for e in data.get("envs", [])]
        return {"envs": envs, "condaBasePath": conda_base_path}
    except Exception as exc:  # noqa: BLE001
        return {"envs": [], "error": str(exc)[:300]}


def browse_dirs(server_id: str, path: str, user: Any) -> dict[str, Any]:
    """List subdirectories on the remote server for the path picker."""
    row = get_server(server_id, user)
    target = (path or "").strip() or "/"
    if not target.startswith("/"):
        target = "/" + target
    target = "/" + "/".join(p for p in target.split("/") if p)

    spec = _ssh_spec(row)
    safe = shlex.quote(target)
    cmd = f"find {safe} -maxdepth 1 -type d ! -name '.*' 2>/dev/null | sort"
    try:
        out, _, _ = ssh_connection_service.exec_command(spec, cmd, timeout=15)
        dirs: list[dict[str, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line == target:
                continue
            name = line.rsplit("/", 1)[-1] if "/" in line else line
            if not name:
                continue
            dirs.append({"name": name, "path": line})
        return {"path": target, "dirs": dirs}
    except Exception as exc:  # noqa: BLE001
        raise ToolboxError("BROWSE_FAILED", f"浏览目录失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc


# ============================================================
# TensorBoard Sessions
# ============================================================


def list_sessions(user: Any) -> list[dict[str, Any]]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        rows = conn.execute(
            "SELECT * FROM tb_sessions WHERE owner_user_id = ? ORDER BY started_at DESC",
            (user.id,),
        ).fetchall()
    return [_public_session(row) for row in rows]


def _launch_tensorboard(
    server: sqlite3.Row,
    logdir: str,
    tb_session_id: str,
    python_mode: str,
    conda_env: str,
    python_path: str,
) -> tuple[int, int, str, str, str]:
    """Launch TensorBoard on the remote server and create an SSH tunnel.

    Returns (remote_port, local_port, remote_pid, status, error).
    """
    remote_port = _find_free_remote_port(server)
    conda_base_path = server["conda_base_path"] if "conda_base_path" in server.keys() else ""
    if python_mode == "conda" and not conda_base_path:
        raise ToolboxError("CONDA_BASE_PATH_REQUIRED", "该服务器未配置 Anaconda 路径，请在服务器设置中填写", status_code=400, tool_id=TOOL_ID)
    tb_cmd = _build_tensorboard_command(
        logdir=logdir,
        remote_port=remote_port,
        tb_session_id=tb_session_id,
        python_mode=python_mode,
        conda_env=conda_env,
        python_path=python_path,
        conda_base_path=conda_base_path,
    )

    start_cmd = f"bash -lc {shlex.quote(tb_cmd)}"
    try:
        out, err, _ = ssh_connection_service.exec_command(
            _ssh_spec(server), start_cmd, timeout=30
        )
    except Exception as exc:  # noqa: BLE001
        raise ToolboxError("TB_START_FAILED", f"TensorBoard 启动失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc

    remote_pid = out.strip().split("\n")[-1].strip()
    if not remote_pid or not remote_pid.isdigit():
        log_cmd = f"cat /tmp/tb_{tb_session_id}.log 2>/dev/null | tail -20"
        try:
            log_out, _, _ = ssh_connection_service.exec_command(
                _ssh_spec(server), log_cmd, timeout=10
            )
        except Exception:  # noqa: BLE001
            log_out = ""
        raise ToolboxError(
            "TB_START_FAILED",
            f"无法获取 TensorBoard 进程 PID。{err[:300] or log_out[:300]}",
            status_code=502,
            tool_id=TOOL_ID,
        )

    try:
        local_port = _start_tunnel(server, tb_session_id, remote_port)
    except Exception as exc:  # noqa: BLE001
        _kill_remote_process(server, remote_pid)
        raise ToolboxError("TUNNEL_FAILED", f"SSH 隧道创建失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc

    time.sleep(2)
    alive = _check_remote_process(server, remote_pid)
    status = "running" if alive else "failed"
    error = ""
    if not alive:
        log_cmd = f"cat /tmp/tb_{tb_session_id}.log 2>/dev/null | tail -20"
        try:
            log_out, _, _ = ssh_connection_service.exec_command(
                _ssh_spec(server), log_cmd, timeout=10
            )
            error = log_out[:500]
        except Exception:  # noqa: BLE001
            error = "TensorBoard 进程已退出"
        _stop_tunnel(tb_session_id)
        _kill_remote_process(server, remote_pid)

    return remote_port, local_port, remote_pid, status, error


def start_session(payload: dict[str, Any], user: Any) -> dict[str, Any]:
    init_database(user.id)
    server_id = _required(payload, "serverId")
    server = get_server(server_id, user)
    name = _required(payload, "name")
    logdir = _required(payload, "logdir")
    python_mode = payload.get("pythonMode", "conda")
    if python_mode not in ("conda", "path"):
        raise ToolboxError("INVALID_PYTHON_MODE", "python 模式必须是 conda 或 path", status_code=400, tool_id=TOOL_ID)
    conda_env = (payload.get("condaEnv") or "").strip()
    python_path = (payload.get("pythonPath") or "").strip()
    if python_mode == "conda" and not conda_env:
        raise ToolboxError("CONDA_ENV_REQUIRED", "conda 模式需要指定 conda 环境名", status_code=400, tool_id=TOOL_ID)
    if python_mode == "path" and not python_path:
        raise ToolboxError("PYTHON_PATH_REQUIRED", "path 模式需要指定 Python 路径", status_code=400, tool_id=TOOL_ID)
    extra_params = (payload.get("extraParams") or "").strip()

    # Limit active sessions to prevent resource exhaustion.
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) as c FROM tb_sessions WHERE owner_user_id = ? AND status IN ('starting', 'running')",
            (user.id,),
        ).fetchone()
    if active_count and active_count["c"] >= 20:
        raise ToolboxError("TOO_MANY_SESSIONS", "活跃会话数过多，请先停止部分会话", status_code=400, tool_id=TOOL_ID)

    session_id = _new_id()
    tb_session_id = _new_id()  # used as path prefix
    now = _now()

    remote_port, local_port, remote_pid, status, error = _launch_tensorboard(
        server, logdir, tb_session_id, python_mode, conda_env, python_path,
    )

    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            """
            INSERT INTO tb_sessions (
                id, owner_user_id, server_id, name, logdir,
                remote_port, local_port, python_mode, conda_env, python_path,
                extra_params, remote_pid, tb_session_id, status, error, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, user.id, server_id, name, logdir,
                remote_port, local_port, python_mode, conda_env, python_path,
                extra_params, remote_pid, tb_session_id, status, error, now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()

    return _public_session(row)


def restart_session(session_id: str, user: Any) -> dict[str, Any]:
    """Restart a stopped or failed session with its current config."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)

    # If still running, stop first.
    if row["status"] in ("starting", "running"):
        stop_session(session_id, user)
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()

    server = get_server(row["server_id"], user)
    tb_session_id = row["tb_session_id"]
    # Stop any lingering tunnel just in case.
    _stop_tunnel(tb_session_id)

    remote_port, local_port, remote_pid, status, error = _launch_tensorboard(
        server,
        row["logdir"],
        tb_session_id,
        row["python_mode"],
        row["conda_env"],
        row["python_path"],
    )

    now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            """
            UPDATE tb_sessions
            SET remote_port = ?, local_port = ?, remote_pid = ?,
                status = ?, error = ?, started_at = ?, stopped_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (remote_port, local_port, remote_pid, status, error, now, now, session_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()
    return _public_session(row)


def stop_session(session_id: str, user: Any) -> dict[str, Any]:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)

    # Kill the remote TB process.
    if row["remote_pid"]:
        server = get_server(row["server_id"], user)
        _kill_remote_process(server, row["remote_pid"])

    # Stop the SSH tunnel.
    _stop_tunnel(row["tb_session_id"])

    now = _now()
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            "UPDATE tb_sessions SET status = 'stopped', stopped_at = ?, updated_at = ? WHERE id = ?", 
            (now, now, session_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()
    return _public_session(row)


def check_session(session_id: str, user: Any) -> dict[str, Any]:
    """Check if a session is still alive. Update status if it died."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)

    if row["status"] in ("stopped", "failed"):
        return _public_session(row)

    server = get_server(row["server_id"], user)
    alive = False
    error = ""
    if row["remote_pid"]:
        alive = _check_remote_process(server, row["remote_pid"])

    tunnel = get_tunnel(row["tb_session_id"])
    tunnel_alive = tunnel is not None and tunnel.is_alive()

    new_status = row["status"]
    if not alive or not tunnel_alive:
        new_status = "failed"
        if not alive:
            error = "TensorBoard 进程已退出"
        elif not tunnel_alive:
            error = "SSH 隧道已断开"
        now = _now()
        _stop_tunnel(row["tb_session_id"])
        with user_tool_connection_context(user.id, TOOL_ID) as conn:
            conn.execute(
                "UPDATE tb_sessions SET status = ?, error = ?, stopped_at = ?, updated_at = ? WHERE id = ?",
                (new_status, error, now, now, session_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()
    return _public_session(row)


def update_session(session_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    """Update session config. Only allowed when stopped/failed."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    if row["status"] in ("starting", "running"):
        raise ToolboxError("SESSION_ACTIVE", "会话运行中，请先停止再编辑", status_code=400, tool_id=TOOL_ID)
    now = _now()
    updates: dict[str, Any] = {}
    if "name" in payload and payload["name"].strip():
        updates["name"] = payload["name"].strip()
    if "logdir" in payload and payload["logdir"].strip():
        updates["logdir"] = payload["logdir"].strip()
    if "pythonMode" in payload and payload["pythonMode"] in ("conda", "path"):
        updates["python_mode"] = payload["pythonMode"]
    if "condaEnv" in payload:
        updates["conda_env"] = (payload["condaEnv"] or "").strip()
    if "pythonPath" in payload:
        updates["python_path"] = (payload["pythonPath"] or "").strip()
    if "extraParams" in payload:
        updates["extra_params"] = (payload["extraParams"] or "").strip()
    if "serverId" in payload and payload["serverId"].strip():
        get_server(payload["serverId"].strip(), user)
        updates["server_id"] = payload["serverId"].strip()
    if not updates:
        return _public_session(row)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [now, session_id]
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute(
            f"UPDATE tb_sessions SET {set_clause}, updated_at = ? WHERE id = ?",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tb_sessions WHERE id = ?", (session_id,)).fetchone()
    return _public_session(row)


def delete_session(session_id: str, user: Any) -> None:
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)

    # Stop if still active.
    if row["status"] in ("starting", "running"):
        stop_session(session_id, user)

    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        conn.execute("DELETE FROM tb_sessions WHERE id = ?", (session_id,))
        conn.commit()


def get_session_url(session_id: str, user: Any) -> dict[str, str]:
    """Return the reverse-proxy URL for accessing the TensorBoard."""
    init_database(user.id)
    with user_tool_connection_context(user.id, TOOL_ID) as conn:
        row = conn.execute(
            "SELECT * FROM tb_sessions WHERE id = ? AND owner_user_id = ?",
            (session_id, user.id),
        ).fetchone()
    if row is None:
        raise ToolboxError("SESSION_NOT_FOUND", "会话不存在或不可访问", status_code=404, tool_id=TOOL_ID)
    url = f"/tb/{row['tb_session_id']}/"
    return {"url": url}


# ============================================================
# Remote process helpers
# ============================================================


def _build_tensorboard_command(
    logdir: str,
    remote_port: int,
    tb_session_id: str,
    python_mode: str,
    conda_env: str,
    python_path: str,
    conda_base_path: str = "",
) -> str:
    """Build the shell command to start TensorBoard in the background.

    For conda mode, we source conda.sh and activate the environment so that
    ``$!`` captures the actual Python PID (not the conda wrapper PID).  This
    ensures ``kill`` can terminate the real tensorboard process.
    """
    path_prefix = f"/tb/{tb_session_id}"
    log_file = f"/tmp/tb_{tb_session_id}.log"

    tb_args = (
        f"-m tensorboard.main"
        f" --logdir {shlex.quote(logdir)}"
        f" --port {remote_port}"
        f" --host 127.0.0.1"
        f" --path_prefix {shlex.quote(path_prefix)}"
    )

    if python_mode == "conda":
        conda_sh = shlex.quote(f"{conda_base_path.rstrip('/')}/etc/profile.d/conda.sh")
        # source conda, activate env, exec python so $! is the python PID
        inner = (
            f"source {conda_sh} && conda activate {shlex.quote(conda_env)}"
            f" && exec python {tb_args}"
        )
    else:
        inner = f"exec {shlex.quote(python_path)} {tb_args}"

    return (
        f"nohup bash -c {shlex.quote(inner)}"
        f" > {shlex.quote(log_file)} 2>&1 &"
        f" echo $!"
    )


def _kill_remote_process(row: sqlite3.Row, pid: str) -> None:
    """Kill a remote process by PID (SIGTERM then SIGKILL)."""
    if not pid:
        return
    spec = _ssh_spec(row)
    try:
        ssh_connection_service.exec_command(spec, f"kill {pid} 2>/dev/null", timeout=5)
        time.sleep(1)
        ssh_connection_service.exec_command(spec, f"kill -9 {pid} 2>/dev/null", timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _check_remote_process(row: sqlite3.Row, pid: str) -> bool:
    """Check if a remote process is still running."""
    if not pid:
        return False
    spec = _ssh_spec(row)
    try:
        out, _, _ = ssh_connection_service.exec_command(
            spec, f"kill -0 {pid} 2>/dev/null && echo RUNNING || echo STOPPED", timeout=8
        )
        return "RUNNING" in out
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# SSH helpers
# ============================================================


def _ssh_connect(row: sqlite3.Row, timeout: int = 20):
    """Borrow a client from the connection pool."""
    return ssh_connection_service.borrow_client(_ssh_spec(row, timeout=timeout))


def _ssh_connect_dedicated(row: sqlite3.Row, timeout: int = 20):
    """Create a dedicated SSH client (not from pool) for long-lived tunnels."""
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    spec = _ssh_spec(row, timeout=timeout)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict[str, Any] = {}
    if spec.pkey is not None:
        kwargs["pkey"] = spec.pkey
    else:
        kwargs["password"] = spec.password
    try:
        client.connect(
            hostname=spec.host,
            port=int(spec.port),
            username=spec.username,
            timeout=spec.connect_timeout,
            banner_timeout=spec.connect_timeout,
            auth_timeout=spec.connect_timeout,
            look_for_keys=False,
            allow_agent=False,
            **kwargs,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        return client
    except Exception as exc:
        client.close()
        raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 连接失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc


def _ssh_exec(client: Any, command: str, timeout: int = 30) -> tuple[str, str, int]:
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return out, err, code
    except Exception:
        if hasattr(client, "invalidate"):
            client.invalidate()
        raise


def _load_private_key(row: sqlite3.Row, paramiko: Any) -> Any:
    key_text = _decrypt(row["private_key_encrypted"])
    passphrase = _decrypt(row["private_key_passphrase_encrypted"]) if row["private_key_passphrase_encrypted"] else None
    key_errors: list[str] = []
    for key_cls_name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
        key_cls = getattr(paramiko, key_cls_name, None)
        if key_cls is None:
            continue
        try:
            return key_cls.from_private_key(io.StringIO(key_text), password=passphrase)
        except Exception as exc:  # noqa: BLE001
            key_errors.append(f"{key_cls_name}: {exc}")
    raise ToolboxError("PRIVATE_KEY_INVALID", "私钥无法解析或 passphrase 不正确", status_code=400, tool_id=TOOL_ID, extra={"details": key_errors[-2:]})


def _ssh_spec(row: sqlite3.Row, timeout: int = 20) -> SSHConnectionSpec:
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("SSH_DEPENDENCY_MISSING", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    auth_type = row["auth_type"]
    if auth_type == "private_key":
        return SSHConnectionSpec(
            tool_id=TOOL_ID,
            server_id=row["id"],
            host=row["host"],
            port=int(row["port"]),
            username=row["ssh_username"],
            auth_fingerprint=ssh_connection_service.auth_fingerprint(
                auth_type,
                row["private_key_encrypted"],
                row["private_key_passphrase_encrypted"],
            ),
            pkey=_load_private_key(row, paramiko),
            connect_timeout=timeout,
        )
    return SSHConnectionSpec(
        tool_id=TOOL_ID,
        server_id=row["id"],
        host=row["host"],
        port=int(row["port"]),
        username=row["ssh_username"],
        auth_fingerprint=ssh_connection_service.auth_fingerprint(auth_type, row["ssh_password_encrypted"]),
        password=_decrypt(row["ssh_password_encrypted"]),
        connect_timeout=timeout,
    )


# ============================================================
# Public serializers
# ============================================================


def _public_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
        "sshUsername": row["ssh_username"], "authType": row["auth_type"],
        "condaBasePath": row["conda_base_path"] if "conda_base_path" in row.keys() else "",
        "lastTestStatus": row["last_test_status"], "lastTestError": row["last_test_error"],
        "lastTestedAt": row["last_tested_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _public_session(row: sqlite3.Row) -> dict[str, Any]:
    extra = row["extra_params"] if "extra_params" in row.keys() else ""
    url = f"/tb/{row['tb_session_id']}/"
    if extra:
        url = url + extra
    return {
        "id": row["id"], "serverId": row["server_id"], "name": row["name"],
        "logdir": row["logdir"], "remotePort": row["remote_port"], "localPort": row["local_port"],
        "pythonMode": row["python_mode"], "condaEnv": row["conda_env"], "pythonPath": row["python_path"],
        "extraParams": extra,
        "remotePid": row["remote_pid"], "tbSessionId": row["tb_session_id"],
        "status": row["status"], "error": row["error"],
        "startedAt": row["started_at"], "stoppedAt": row["stopped_at"],
        "url": url,
    }


# ============================================================
# Misc helpers
# ============================================================


def _get_fernet() -> Fernet:
    secret = get_settings().session_secret
    key = base64.urlsafe_b64encode(secret.encode("utf-8").ljust(32)[:32])
    return Fernet(key)


def _encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("DECRYPT_ERROR", "凭证解密失败", status_code=500, tool_id=TOOL_ID) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_hex(12)


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ToolboxError("INVALID_PAYLOAD", f"{key} 不能为空", status_code=400, tool_id=TOOL_ID)
    return value


def _clean_auth_type(value: Any) -> str:
    auth_type = str(value or "password")
    if auth_type not in {"password", "private_key"}:
        raise ToolboxError("INVALID_AUTH_TYPE", "认证方式不合法", status_code=400, tool_id=TOOL_ID)
    return auth_type


def _validate_server_payload(payload: dict[str, Any], auth_type: str, creating: bool) -> None:
    for key in ("name", "host", "sshUsername"):
        if creating or key in payload:
            _required(payload, key)
    if auth_type == "password" and creating and not payload.get("sshPassword"):
        raise ToolboxError("PASSWORD_REQUIRED", "密码登录需要填写 SSH 密码", status_code=400, tool_id=TOOL_ID)
    if auth_type == "private_key" and creating and not payload.get("privateKey"):
        raise ToolboxError("PRIVATE_KEY_REQUIRED", "私钥登录需要填写私钥内容", status_code=400, tool_id=TOOL_ID)
