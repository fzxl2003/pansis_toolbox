import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

from backend.app.core.config import get_settings
from backend.app.db.database import get_user_tool_db_path
from backend.app.services.auth_service import User
from backend.app.services.scheduler_service import Scheduler
from tools.server_monitor.backend import service
from tools.server_monitor.backend.migrate_history import migrate_database
from tools.server_monitor.backend.scheduler import register_tasks
from tools.server_monitor.backend.trends import SAMPLES_SCHEMA, read_history, store_sample

NOW = datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc)


def test_history_intervals_peaks_and_gpu_identity():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE monitor_servers (id TEXT PRIMARY KEY)")
        connection.execute(SAMPLES_SCHEMA)
        for timestamp, cpu, total, used, gpu in [
            (NOW - timedelta(minutes=11), 0, 20, 0, {"index": 0, "utilizationPercent": 0}),
            (NOW - timedelta(minutes=1), 20, 100, 25, {"index": 0, "uuid": "GPU-A", "utilizationPercent": 60}),
            (NOW, 80, 200, 100, {"index": 1, "uuid": "GPU-A", "utilizationPercent": 80}),
        ]:
            assert store_sample(connection, "server", timestamp, cpu, total, used, [gpu]) == 1
            assert store_sample(connection, "server", timestamp, 100, total, used, []) == 0

        history = read_history(connection, NOW, "server", 24)
        assert history["bucketSeconds"] == 300
        assert len(history["samples"]) == 288
        first, gap, last = history["samples"][-3:]
        assert first["cpuPercent"] == {"avg": 0, "max": 0}
        assert first["gpus"][0]["uuid"] is None
        assert gap["cpuPercent"] is None and gap["gpus"] == []
        assert last == {
            "collectedAt": "2026-01-01T12:00:00+00:00",
            "cpuPercent": {"avg": 50, "max": 80},
            "memoryPercent": {"avg": 37.5, "max": 50},
            "gpus": [{"index": 1, "uuid": "GPU-A", "utilizationPercent": {"avg": 70, "max": 80}, "memoryPercent": None}],
        }
        month = read_history(connection, NOW, "server", 720)
        assert month["bucketSeconds"] == 9000
        assert len(month["samples"]) == 288


def test_snapshot_coalescing_cache_and_forced_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_snapshots", {})
    monkeypatch.setattr(service, "_snapshot_requests", {})
    monkeypatch.setattr(service, "user_tool_connection_context", lambda *_: sqlite3.connect(tmp_path / "data.db"))
    with sqlite3.connect(tmp_path / "data.db") as connection:
        connection.execute("CREATE TABLE monitor_servers (id TEXT PRIMARY KEY)")
        connection.execute(SAMPLES_SCHEMA)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz):
            return NOW

    monkeypatch.setattr(service, "datetime", FixedDatetime)
    collecting = threading.Event()
    release = threading.Event()
    joined = threading.Event()
    calls = []

    def ssh(row, command, timeout=20):
        calls.append(command)
        collecting.set()
        assert release.wait(5)
        return "__MEMINFO__\nMemTotal: 100 kB\nMemAvailable: 50 kB\n"

    class ObservedFuture(service.Future):
        def result(self, *args, **kwargs):
            joined.set()
            return super().result(*args, **kwargs)

    monkeypatch.setattr(service, "_run_ssh", ssh)
    monkeypatch.setattr(service, "Future", ObservedFuture)
    row = {"id": "server"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service._snapshot, "user", row)
        assert collecting.wait(5)
        second = pool.submit(service._snapshot, "user", row, force=True)
        try:
            assert joined.wait(5)
        finally:
            release.set()
        assert first.result() == second.result()
    assert service._snapshot("user", row) == first.result()
    assert len(calls) == 1
    fresh = service._snapshot("user", row, force=True)
    assert fresh["id"] != first.result()["id"]
    assert len(calls) == 2
    with sqlite3.connect(tmp_path / "data.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM monitor_samples").fetchone() == (1,)

    def unavailable(*args):
        raise RuntimeError("SSH unavailable")

    monkeypatch.setattr(service, "_run_ssh", unavailable)
    assert service._snapshot("user", row, force=True)["error"] == "SSH unavailable"
    with sqlite3.connect(tmp_path / "data.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM monitor_samples").fetchone() == (1,)


def test_offline_migration_preserves_configuration_and_compacts_history(tmp_path):
    path = tmp_path / "data.db"
    backup = tmp_path / "backup" / "data.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE monitor_servers (id TEXT PRIMARY KEY);
            INSERT INTO monitor_servers VALUES ('server');
            CREATE TABLE monitor_directory_cache (path TEXT);
            INSERT INTO monitor_directory_cache VALUES ('/data');
            CREATE TABLE monitor_samples (
                id TEXT PRIMARY KEY, server_id TEXT REFERENCES monitor_servers(id), collected_at TEXT,
                cpu_percent REAL, memory_total_bytes INTEGER, memory_used_bytes INTEGER,
                disks_json TEXT, gpus_json TEXT, error TEXT
            );
            CREATE INDEX idx_monitor_samples_server_time ON monitor_samples(server_id, collected_at);
        """)
        gpu = json.dumps([{
            "index": 0, "uuid": "GPU-A", "utilizationPercent": 99, "memoryUsedMiB": 2,
            "memoryTotalMiB": 8, "processes": [{"command": "train " * 3000}],
        }])
        connection.executemany(
            "INSERT INTO monitor_samples VALUES (?, 'server', ?, 25, 100, 50, '[]', ?, ?)",
            [(i, timestamp.isoformat(), gpu, error) for i, (timestamp, error) in enumerate([
                (NOW - timedelta(days=31), None), (NOW - timedelta(seconds=29), None),
                (NOW - timedelta(seconds=24), None), (NOW, "SSH error"),
            ])],
        )
    report = migrate_database(path, backup, NOW)
    assert report["before"]["rows"] == 4
    assert report["after"]["rows"] == 1
    assert report["after"]["filesBytes"]["main"] < report["before"]["filesBytes"]["main"]
    assert migrate_database(path, backup, NOW)["status"] == "current"
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM monitor_samples").fetchone() == (4,)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT path FROM monitor_directory_cache").fetchone() == ("/data",)
        assert connection.execute("SELECT id FROM monitor_servers").fetchone() == ("server",)
        result = read_history(connection, NOW, "server", 24)["samples"][-1]
        assert result["cpuPercent"] == {"avg": 25, "max": 25}
        assert result["memoryPercent"] == {"avg": 50, "max": 50}
        assert result["gpus"][0]["uuid"] == "GPU-A"
        assert result["gpus"][0]["memoryPercent"] == {"avg": 25, "max": 25}
        assert "processes" not in connection.execute("SELECT gpus_json FROM monitor_samples").fetchone()[0]


def test_first_history_request_upgrades_old_db_and_background_retries_compaction(monkeypatch, tmp_path):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz):
            return NOW

    monkeypatch.setattr(service, "datetime", FixedDatetime)
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    monkeypatch.setattr(service, "_initialized_dbs", set())
    monkeypatch.setattr(service, "_migrate_legacy_default_servers", lambda: None)
    monkeypatch.setattr(service.ssh_server_service, "merge_connection_credentials", lambda row, user: dict(row))
    user = User(id="user", username="user", display_name="User")
    path = get_user_tool_db_path(user.id, service.TOOL_ID)
    service.init_monitor_database(user.id)
    service._initialized_dbs.clear()

    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            INSERT INTO monitor_servers (
                id, name, host, ssh_username, ssh_password_encrypted, owner_user_id, created_at, updated_at
            ) VALUES ('server', 'Server', 'localhost', 'user', '', 'user', '', '');
            INSERT INTO monitor_directory_cache (id, server_id, user_id, path)
                VALUES ('dir', 'server', 'user', '/data');
            DROP TABLE monitor_samples;
            CREATE TABLE monitor_samples (
                id TEXT PRIMARY KEY, server_id TEXT REFERENCES monitor_servers(id), collected_at TEXT,
                cpu_percent REAL, memory_total_bytes INTEGER, memory_used_bytes INTEGER,
                disks_json TEXT, gpus_json TEXT, error TEXT
            );
        """)
        db.execute(
            "INSERT INTO monitor_samples VALUES ('sample', 'server', ?, 40, 100, 20, ?, '[]', NULL)",
            (NOW.isoformat(), json.dumps([{"mountPath": "x" * 60_000}])),
        )
    legacy_size = path.stat().st_size

    scheduler = Scheduler()
    register_tasks(scheduler)
    upgrade = next(task for task in scheduler.list_tasks() if task.name == "upgrade_history_databases")
    assert upgrade.run_immediately and upgrade.interval_seconds == 60

    assert service.history("server", user)["samples"][-1]["cpuPercent"] == {"avg": 40, "max": 40}
    pending_size = path.stat().st_size

    # Hold a SQLite read transaction so the first background VACUUM cannot run.
    with closing(sqlite3.connect(path)) as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM monitor_samples").fetchone() == (1,)
        service.upgrade_history_databases()
        assert reader.execute("PRAGMA freelist_count").fetchone()[0] > 0
        assert path.stat().st_size == pending_size

    service._initialized_dbs.clear()
    service.upgrade_history_databases()
    assert path.stat().st_size < legacy_size
    service.upgrade_history_databases()

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA freelist_count").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM monitor_samples").fetchone() == (1,)
        assert db.execute("SELECT path FROM monitor_directory_cache").fetchone() == ("/data",)
    assert service.history("server", user)["samples"][-1]["memoryPercent"] == {"avg": 20, "max": 20}
