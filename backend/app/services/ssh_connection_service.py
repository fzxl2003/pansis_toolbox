from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

from backend.app.core.errors import ToolboxError

KEEPALIVE_SECONDS = 30
IDLE_TTL_SECONDS = 600
DEFAULT_MAX_CLIENTS_PER_KEY = 2


@dataclass(frozen=True)
class SSHConnectionSpec:
    tool_id: str
    server_id: str
    host: str
    port: int
    username: str
    auth_fingerprint: str
    password: str | None = None
    pkey: Any | None = None
    connect_timeout: int = 20
    connect_error_code: str = "SSH_CONNECT_FAILED"
    missing_dependency_code: str = "SSH_DEPENDENCY_MISSING"
    missing_dependency_message: str = "缺少 paramiko 依赖，无法执行 SSH 命令"

    @property
    def pool_key(self) -> tuple[str, str, str, int, str, str]:
        return (self.tool_id, self.server_id, self.host, int(self.port), self.username, self.auth_fingerprint)


@dataclass
class _PoolEntry:
    client: Any
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)


class SSHClientLease:
    """Proxy for pooled Paramiko clients.

    Existing tool code calls `client.close()` in finally blocks. On a lease this
    releases the pool lock instead of closing the underlying transport.
    """

    def __init__(self, pool: "SSHConnectionPool", key: tuple[str, str, str, int, str, str], entry: _PoolEntry):
        self._pool = pool
        self._key = key
        self._entry = entry
        self._released = False

    @property
    def raw_client(self) -> Any:
        return self._entry.client

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self._pool._release(self._key, self._entry)

    def invalidate(self) -> None:
        if self._released:
            return
        self._released = True
        self._pool._invalidate_entry(self._key, self._entry)

    def __enter__(self) -> "SSHClientLease":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc is not None and _is_transport_error(exc):
            self.invalidate()
            return
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._entry.client, name)


class SSHConnectionPool:
    def __init__(
        self,
        max_clients_per_key: int = DEFAULT_MAX_CLIENTS_PER_KEY,
        idle_ttl_seconds: int = IDLE_TTL_SECONDS,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.max_clients_per_key = max(1, int(max_clients_per_key))
        self.idle_ttl_seconds = max(30, int(idle_ttl_seconds))
        self._client_factory = client_factory
        self._entries: dict[tuple[str, str, str, int, str, str], list[_PoolEntry]] = {}
        self._pool_lock = threading.Lock()
        self._last_cleanup = 0.0

    def borrow_client(self, spec: SSHConnectionSpec) -> SSHClientLease:
        self._cleanup_idle_if_due()
        key = spec.pool_key
        while True:
            with self._pool_lock:
                entries = self._entries.setdefault(key, [])
                for entry in list(entries):
                    if not _client_is_active(entry.client):
                        entries.remove(entry)
                        _close_client(entry.client)
                        continue
                    if entry.lock.acquire(blocking=False):
                        entry.last_used = time.monotonic()
                        return SSHClientLease(self, key, entry)
                if len(entries) < self.max_clients_per_key:
                    entry = _PoolEntry(client=self._connect(spec))
                    entry.lock.acquire()
                    entries.append(entry)
                    return SSHClientLease(self, key, entry)
                wait_entry = entries[0]
            wait_entry.lock.acquire()
            with self._pool_lock:
                if wait_entry not in self._entries.get(key, []):
                    wait_entry.lock.release()
                    continue
                if not _client_is_active(wait_entry.client):
                    self._entries[key].remove(wait_entry)
                    wait_entry.lock.release()
                    _close_client(wait_entry.client)
                    continue
                wait_entry.last_used = time.monotonic()
                return SSHClientLease(self, key, wait_entry)

    @contextmanager
    def borrowed_client(self, spec: SSHConnectionSpec) -> Generator[SSHClientLease, None, None]:
        lease = self.borrow_client(spec)
        try:
            yield lease
        except Exception as exc:
            if _is_transport_error(exc):
                lease.invalidate()
            else:
                lease.close()
            raise
        else:
            lease.close()

    def exec_command(self, spec: SSHConnectionSpec, command: str, timeout: int = 30) -> tuple[str, str, int]:
        with self.borrowed_client(spec) as client:
            try:
                _, stdout, stderr = client.exec_command(command, timeout=timeout)
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                code = stdout.channel.recv_exit_status()
                return out, err, code
            except Exception:
                client.invalidate()
                raise

    def invalidate(
        self,
        *,
        tool_id: str | None = None,
        server_id: str | None = None,
        key: tuple[str, str, str, int, str, str] | None = None,
    ) -> None:
        with self._pool_lock:
            keys = [key] if key is not None else list(self._entries.keys())
            for pool_key in keys:
                if pool_key is None:
                    continue
                if tool_id is not None and pool_key[0] != tool_id:
                    continue
                if server_id is not None and pool_key[1] != server_id:
                    continue
                entries = self._entries.pop(pool_key, [])
                for entry in entries:
                    _close_client(entry.client)
                    if entry.lock.locked():
                        try:
                            entry.lock.release()
                        except RuntimeError:
                            pass

    def close_all(self) -> None:
        self.invalidate()

    def _connect(self, spec: SSHConnectionSpec) -> Any:
        try:
            client = self._new_client()
        except ImportError as exc:
            raise ToolboxError(
                spec.missing_dependency_code,
                spec.missing_dependency_message,
                status_code=500,
                tool_id=spec.tool_id,
            ) from exc
        try:
            client.set_missing_host_key_policy(self._auto_add_policy())
            kwargs: dict[str, Any] = {}
            if spec.pkey is not None:
                kwargs["pkey"] = spec.pkey
            else:
                kwargs["password"] = spec.password
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
                transport.set_keepalive(KEEPALIVE_SECONDS)
            return client
        except ToolboxError:
            _close_client(client)
            raise
        except Exception as exc:
            _close_client(client)
            raise ToolboxError(
                spec.connect_error_code,
                f"SSH 连接失败: {exc}",
                status_code=502,
                tool_id=spec.tool_id,
            ) from exc

    def _new_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import paramiko

        return paramiko.SSHClient()

    def _auto_add_policy(self) -> Any:
        if self._client_factory is not None:
            return object()
        import paramiko

        return paramiko.AutoAddPolicy()

    def _release(self, key: tuple[str, str, str, int, str, str], entry: _PoolEntry) -> None:
        entry.last_used = time.monotonic()
        try:
            entry.lock.release()
        except RuntimeError:
            pass

    def _invalidate_entry(self, key: tuple[str, str, str, int, str, str], entry: _PoolEntry) -> None:
        with self._pool_lock:
            entries = self._entries.get(key, [])
            if entry in entries:
                entries.remove(entry)
            if not entries and key in self._entries:
                self._entries.pop(key, None)
        _close_client(entry.client)
        try:
            entry.lock.release()
        except RuntimeError:
            pass

    def _cleanup_idle_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        with self._pool_lock:
            for key, entries in list(self._entries.items()):
                keep: list[_PoolEntry] = []
                for entry in entries:
                    if entry.lock.locked() or now - entry.last_used < self.idle_ttl_seconds:
                        keep.append(entry)
                    else:
                        _close_client(entry.client)
                if keep:
                    self._entries[key] = keep
                else:
                    self._entries.pop(key, None)


def auth_fingerprint(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def borrow_client(spec: SSHConnectionSpec) -> SSHClientLease:
    return _GLOBAL_POOL.borrow_client(spec)


@contextmanager
def borrowed_client(spec: SSHConnectionSpec) -> Generator[SSHClientLease, None, None]:
    with _GLOBAL_POOL.borrowed_client(spec) as client:
        yield client


def exec_command(spec: SSHConnectionSpec, command: str, timeout: int = 30) -> tuple[str, str, int]:
    return _GLOBAL_POOL.exec_command(spec, command, timeout=timeout)


def invalidate(*, tool_id: str | None = None, server_id: str | None = None) -> None:
    _GLOBAL_POOL.invalidate(tool_id=tool_id, server_id=server_id)


def close_all() -> None:
    _GLOBAL_POOL.close_all()


def _client_is_active(client: Any) -> bool:
    try:
        transport = client.get_transport()
        return bool(transport and transport.is_active())
    except Exception:
        return False


def _close_client(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass


def _is_transport_error(exc: object) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    markers = ("ssh", "socket", "transport", "connection reset", "connection refused", "connection aborted", "broken pipe")
    return any(marker in name or marker in text for marker in markers)


_GLOBAL_POOL = SSHConnectionPool()
