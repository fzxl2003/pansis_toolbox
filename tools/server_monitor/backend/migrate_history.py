"""Upgrade old per-user monitor histories and reclaim their freed pages."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.core.config import get_settings
from tools.server_monitor.backend.trends import RETENTION_DAYS, SAMPLES_SCHEMA, SAMPLES_TIME_INDEX, store_sample


# Reserved for the dedicated server_monitor database. The pending value is
# committed with the table swap so cleanup survives crashes and restarts.
_COMPACTION_PENDING = 1
_COMPACTION_COMPLETE = 2


def _sample_columns(connection: sqlite3.Connection) -> set[str]:
    return {column[1] for column in connection.execute("PRAGMA table_info(monitor_samples)")}


def migrate_history_schema(connection: sqlite3.Connection, now: datetime) -> bool:
    columns = _sample_columns(connection)
    if not columns or "sample_slot" in columns:
        return False

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # Another process may have finished the migration while we waited.
        if "sample_slot" in _sample_columns(connection):
            return False

        connection.execute("ALTER TABLE monitor_samples RENAME TO monitor_samples_legacy")
        connection.execute(SAMPLES_SCHEMA)
        rows = connection.execute(
            """
            SELECT server_id, collected_at, cpu_percent, memory_total_bytes, memory_used_bytes, gpus_json
            FROM monitor_samples_legacy
            WHERE collected_at >= ? AND collected_at <= ? AND error IS NULL
            ORDER BY server_id, collected_at, rowid
            """,
            ((now - timedelta(days=RETENTION_DAYS)).isoformat(), now.isoformat()),
        )
        for server_id, timestamp, cpu, total, used, gpus in rows:
            store_sample(
                connection, server_id, datetime.fromisoformat(timestamp), cpu, total, used, json.loads(gpus),
            )
        if connection.execute("PRAGMA foreign_key_check(monitor_samples)").fetchone():
            raise RuntimeError("migrated samples failed SQLite foreign_key_check")
        connection.execute("DROP TABLE monitor_samples_legacy")
        connection.execute(SAMPLES_TIME_INDEX)
        connection.execute(f"PRAGMA user_version = {_COMPACTION_PENDING}")
    return True


def compact_history_database(connection: sqlite3.Connection) -> bool:
    if connection.execute("PRAGMA user_version").fetchone()[0] != _COMPACTION_PENDING:
        return False

    connection.execute("VACUUM")
    if connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        busy, *_ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            return False
    connection.execute(f"PRAGMA user_version = {_COMPACTION_COMPLETE}")
    return True


def migrate_database(path: Path, backup_path: Path, now: datetime) -> dict:
    """Optional offline backup and migration, using the same schema upgrade as the app."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        columns = _sample_columns(connection)
        if not columns:
            return {"database": str(path), "status": "no history table"}
        legacy = "sample_slot" not in columns
        before = _stats(connection, path, legacy=legacy)
        if legacy:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            try:
                with closing(sqlite3.connect(backup_path)) as backup:
                    connection.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("backup failed SQLite quick_check")
            except Exception:
                backup_path.unlink()
                raise
            connection.execute("PRAGMA foreign_keys = ON")
            migrate_history_schema(connection, now)

        compact_history_database(connection)
        return {
            "database": str(path), "status": "migrated" if legacy else "current",
            "backup": str(backup_path) if legacy else None, "before": before,
            "after": _stats(connection, path, legacy=False),
        }


def _stats(connection: sqlite3.Connection, path: Path, *, legacy: bool) -> dict:
    disks_size = "SUM(LENGTH(CAST(disks_json AS BLOB)))" if legacy else "0"
    rows, first, last, disks, gpus = connection.execute(f"""
        SELECT COUNT(*), MIN(collected_at), MAX(collected_at),
               {disks_size}, SUM(LENGTH(CAST(gpus_json AS BLOB)))
        FROM monitor_samples
    """).fetchone()
    return {
        "filesBytes": {
            suffix or "main": candidate.stat().st_size if candidate.exists() else 0
            for suffix in ("", "-wal", "-shm", "-journal")
            for candidate in [path.with_name(path.name + suffix)]
        },
        "rows": rows, "firstSample": first, "lastSample": last,
        "diskJsonBytes": disks or 0, "gpuJsonBytes": gpus or 0,
        "freePages": connection.execute("PRAGMA freelist_count").fetchone()[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-dir", type=Path, default=get_settings().storage_dir)
    parser.add_argument("--backup-dir", type=Path, required=True, help="Directory outside the active storage tree")
    args = parser.parse_args()
    if args.storage_dir.resolve() in (args.backup_dir.resolve(), *args.backup_dir.resolve().parents):
        parser.error("--backup-dir must be outside --storage-dir")
    now = datetime.now(timezone.utc)
    run_dir = args.backup_dir / now.strftime("%Y%m%dT%H%M%S%fZ")
    for path in sorted(args.storage_dir.glob("user_data/*/tools/server_monitor/data.db")):
        report = migrate_database(path, run_dir / path.relative_to(args.storage_dir), now)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
