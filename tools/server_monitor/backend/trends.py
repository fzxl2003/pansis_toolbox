from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from itertools import groupby
from typing import Any, Iterable

SAMPLE_SECONDS = 30
RETENTION_DAYS = 30
BUCKET_SECONDS = 300
MAX_BUCKETS = 288

SAMPLES_SCHEMA = """
    CREATE TABLE IF NOT EXISTS monitor_samples (
        server_id TEXT NOT NULL REFERENCES monitor_servers(id),
        sample_slot INTEGER NOT NULL,
        collected_at TEXT NOT NULL,
        cpu_percent REAL,
        memory_total_bytes INTEGER,
        memory_used_bytes INTEGER,
        gpus_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (server_id, sample_slot)
    ) WITHOUT ROWID
"""
SAMPLES_TIME_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_monitor_samples_time ON monitor_samples(collected_at)
"""
GPU_FIELDS = ("uuid", "index", "utilizationPercent", "memoryTotalMiB", "memoryUsedMiB")


def store_sample(
    connection: sqlite3.Connection,
    server_id: str,
    collected_at: datetime,
    cpu: float | None,
    memory_total: int | None,
    memory_used: int | None,
    gpus: list[dict[str, Any]],
) -> int:
    cpu = _valid_percent(cpu)
    if _percent(memory_used, memory_total) is None:
        memory_used = memory_total = None
    compact_gpus = []
    for gpu in gpus:
        utilization = _valid_percent(gpu.get("utilizationPercent"))
        memory_valid = _percent(gpu.get("memoryUsedMiB"), gpu.get("memoryTotalMiB")) is not None
        if utilization is None and not memory_valid:
            continue
        compact = {field: gpu[field] for field in GPU_FIELDS if gpu.get(field) is not None}
        compact["utilizationPercent"] = utilization
        if not memory_valid:
            compact.pop("memoryUsedMiB", None)
            compact.pop("memoryTotalMiB", None)
        compact_gpus.append(compact)
    if cpu is None and memory_used is None and not compact_gpus:
        return 0
    return connection.execute(
        """
        INSERT INTO monitor_samples (
            server_id, sample_slot, collected_at, cpu_percent,
            memory_total_bytes, memory_used_bytes, gpus_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(server_id, sample_slot) DO NOTHING
        """,
        (
            server_id, int(collected_at.timestamp()) // SAMPLE_SECONDS,
            collected_at.isoformat(), cpu, memory_total, memory_used,
            json.dumps(compact_gpus, separators=(",", ":")),
        ),
    ).rowcount


def read_history(connection: sqlite3.Connection, now: datetime, server_id: str, hours: int) -> dict[str, Any]:
    duration = max(1, min(hours, 24 * RETENTION_DAYS)) * 3600
    bucket_seconds = BUCKET_SECONDS * math.ceil(duration / (BUCKET_SECONDS * MAX_BUCKETS))
    count = math.ceil(duration / bucket_seconds)
    end = int(now.timestamp()) // bucket_seconds * bucket_seconds + bucket_seconds
    # Keep the current partial bucket within the point budget. Clip the first
    # bucket to the requested lookback when its width does not divide it evenly.
    start = end - count * bucket_seconds
    since = now - timedelta(seconds=duration)

    rows = connection.execute(
        """
        SELECT sample_slot, cpu_percent, memory_total_bytes, memory_used_bytes, gpus_json
        FROM monitor_samples
        WHERE server_id = ? AND sample_slot BETWEEN ? AND ?
          AND collected_at BETWEEN ? AND ?
        ORDER BY sample_slot
        """,
        (
            server_id, max(start, int(since.timestamp())) // SAMPLE_SECONDS,
            int(now.timestamp()) // SAMPLE_SECONDS, since.isoformat(), now.isoformat(),
        ),
    )
    buckets = {
        timestamp: _summarize(rows)
        for timestamp, rows in groupby(rows, lambda row: row[0] * SAMPLE_SECONDS // bucket_seconds * bucket_seconds)
    }
    return {
        "bucketSeconds": bucket_seconds,
        "samples": [
            {
                "collectedAt": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                **buckets.get(timestamp, {"cpuPercent": None, "memoryPercent": None, "gpus": []}),
            }
            for timestamp in range(start, end, bucket_seconds)
        ],
    }


def _summarize(rows: Iterable[sqlite3.Row]) -> dict[str, Any]:
    cpus, memories, gpu_samples = [], [], []
    uuids_by_index: dict[int, set[str]] = {}
    for _, cpu, total, used, gpu_json in rows:
        cpus.append(cpu)
        memories.append(_percent(used, total))
        for gpu in json.loads(gpu_json):
            if gpu.get("uuid"):
                uuids_by_index.setdefault(gpu["index"], set()).add(gpu["uuid"])
            gpu_samples.append(gpu)

    devices: dict[str | int, tuple[dict[str, Any], list, list]] = {}
    for gpu in gpu_samples:
        index = gpu["index"]
        candidates = uuids_by_index.get(index, set())
        uuid = gpu.get("uuid") or (next(iter(candidates)) if len(candidates) == 1 else None)
        identity, loads, memory = devices.setdefault(uuid or index, ({"index": index, "uuid": uuid}, [], []))
        if gpu.get("uuid") or uuid is None:
            identity["index"] = index
        loads.append(gpu.get("utilizationPercent"))
        memory.append(_percent(gpu.get("memoryUsedMiB"), gpu.get("memoryTotalMiB")))
    return {
        "cpuPercent": _stats(cpus),
        "memoryPercent": _stats(memories),
        "gpus": [
            {**identity, "utilizationPercent": _stats(loads), "memoryPercent": _stats(memory)}
            for identity, loads, memory in devices.values()
        ],
    }


def _stats(values: list[float | None]) -> dict[str, float] | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return {"avg": round(sum(valid) / len(valid), 2), "max": round(max(valid), 2)}


def _percent(used: float | None, total: float | None) -> float | None:
    if used is None or total is None or not (0 <= used <= total) or not math.isfinite(total) or total == 0:
        return None
    return used / total * 100


def _valid_percent(value: float | None) -> float | None:
    return value if value is not None and 0 <= value <= 100 else None
