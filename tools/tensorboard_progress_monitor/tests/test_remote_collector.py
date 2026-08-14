from __future__ import annotations

import base64
import json
import struct
import subprocess
import sys
import sqlite3
import time
from pathlib import Path

from tools.tensorboard_progress_monitor.backend.service import REMOTE_COLLECTOR_SOURCE, _invalidate_task_history, _make_report, _parse_config, _same_collection_config, _trim_report_history, _yaml_hash


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _field(number: int, wire: int, value: bytes) -> bytes:
    prefix = _varint((number << 3) | wire)
    return prefix + (_varint(len(value)) + value if wire == 2 else value)


def _crc32c(data: bytes) -> int:
    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
    return (~value) & 0xFFFFFFFF


def _masked_crc(data: bytes) -> int:
    value = _crc32c(data)
    return (((value >> 15) | (value << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _record(payload: bytes) -> bytes:
    length = struct.pack("<Q", len(payload))
    return length + struct.pack("<I", _masked_crc(length)) + payload + struct.pack("<I", _masked_crc(payload))


def _scalar_event(step: int, scalar: float, wall_time: float) -> bytes:
    value = _field(1, 2, b"train/step") + _field(2, 5, struct.pack("<f", scalar))
    summary = _field(1, 2, value)
    return _field(1, 1, struct.pack("<d", wall_time)) + _field(2, 0, _varint(step)) + _field(5, 2, summary)


def _tensor_scalar_event(step: int, scalar: float, wall_time: float) -> bytes:
    tensor = _field(1, 0, _varint(1)) + _field(4, 2, struct.pack("<f", scalar))
    value = _field(1, 2, b"train/step") + _field(8, 2, tensor)
    summary = _field(1, 2, value)
    return _field(1, 1, struct.pack("<d", wall_time)) + _field(2, 0, _varint(step)) + _field(5, 2, summary)


def _collect(root: Path, mode: str, previous_files: dict[str, dict[str, int]] | None = None) -> dict:
    request = base64.b64encode(json.dumps({"root": str(root), "progress_tag": "train/step", "progress_mode": mode, "tail_bytes": 4096, "previous_files": previous_files or {}}).encode()).decode()
    completed = subprocess.run([sys.executable, "-", "--request-base64", request], input=REMOTE_COLLECTOR_SOURCE, text=True, capture_output=True, check=False)
    assert completed.stderr == ""
    assert completed.returncode == 0
    return json.loads(completed.stdout)


def test_remote_collector_recovers_from_partial_tail_and_uses_event_step(tmp_path: Path) -> None:
    run = tmp_path / "run_a"; run.mkdir()
    (run / "events.out.tfevents.1700000000.host.1").write_bytes(b"cut-record" + _record(_scalar_event(120, 88.0, 1700000120.0)))
    result = _collect(tmp_path, "event_step")
    assert result["errors"] == []
    assert result["runs"][0]["start_hint"] == 1700000000.0
    assert result["runs"][0]["latest"]["progress"] == 120.0
    assert result["read_file_count"] == 1


def test_remote_collector_scalar_mode_ignores_bad_record(tmp_path: Path) -> None:
    run = tmp_path / "run_b"; run.mkdir()
    event = _record(_scalar_event(99, 42.5, 1700000999.0))
    (run / "events.out.tfevents.1700000001.host.2").write_bytes(b"bad tfrecord bytes" + event)
    result = _collect(tmp_path, "scalar_value")
    assert result["runs"][0]["latest"]["progress"] == 42.5


def test_remote_collector_reads_tensor_proto_scalar(tmp_path: Path) -> None:
    run = tmp_path / "run_tensor"; run.mkdir()
    (run / "events.out.tfevents.1700000002.host.3").write_bytes(_record(_tensor_scalar_event(55, 12.25, 1700001002.0)))
    result = _collect(tmp_path, "scalar_value")
    assert result["runs"][0]["latest"]["progress"] == 12.25


def test_remote_collector_skips_unchanged_event_files_without_opening_them(tmp_path: Path) -> None:
    run = tmp_path / "run_skip"; run.mkdir()
    (run / "events.out.tfevents.1700000003.host.4").write_bytes(_record(_scalar_event(7, 7.0, 1700001003.0)))
    first = _collect(tmp_path, "event_step")
    prior = {item["path"]: {"size": item["size"], "mtime_ns": item["mtime_ns"]} for item in first["files"]}
    second = _collect(tmp_path, "event_step", prior)
    assert second["read_file_count"] == 0
    assert second["skipped_file_count"] == 1
    assert second["runs"][0]["latest"] is None


def test_parse_config_rejects_invalid_progress_mode() -> None:
    bad = "tensorboard_root: /logs\nprogress_tag: step\nprogress_mode: invalid\n"
    try:
        _parse_config(bad)
    except Exception as exc:
        assert "progress_mode" in str(exc)
    else:
        raise AssertionError("expected invalid configuration")


def test_report_uses_recent_progress_median_duration_and_concurrency_eta() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    earlier = time.time() - 10
    conn.execute("INSERT INTO tpm_reports VALUES ('old', ?, 1)", (_time_iso(earlier),))
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'active', 'group', 50, NULL, 'running', 'old')")
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 180, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 200.0, "total_runs": 3, "children": []}],
    }
    now = time.time()
    remote = {"errors": [], "runs": [
        {"relative_path": "active", "start_hint": now - 20, "event_file_count": 1, "latest": {"progress": 100.0, "event_time": now}},
        {"relative_path": "done", "start_hint": now - 100, "event_file_count": 1, "latest": {"progress": 200.0, "event_time": now}},
    ]}
    summary, runs = _make_report(conn, {"id": "task"}, {}, config, remote)
    active = next(item for item in runs if item["runKey"] == "active")
    assert active["status"] == "running"
    assert active["etaSeconds"] is not None and 15 < active["etaSeconds"] < 25
    group = summary["groups"][0]
    assert group["medianDurationSeconds"] is not None and 95 < group["medianDurationSeconds"] < 105
    assert group["etaSeconds"] is not None and 115 < group["etaSeconds"] < 125
    assert summary["overallEtaSeconds"] == group["etaSeconds"]


def test_overall_eta_schedules_individual_runs_across_concurrency_slots() -> None:
    """A single category must not collapse all of its work into one serial job."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    now = time.time()
    # Two in-progress runs each have about 100 seconds left.  One completed
    # historical run supplies the 100-second duration for one queued run.
    conn.execute("INSERT INTO tpm_reports VALUES ('active_old', ?, 1)", (_time_iso(now - 100),))
    conn.execute("INSERT INTO tpm_reports VALUES ('done_old', ?, 1)", (_time_iso(now - 120),))
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'active_a', 'group', 0, NULL, 'running', 'active_old')")
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'active_b', 'group', 0, NULL, 'running', 'active_old')")
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'historic_done', 'group', 200, 100, 'completed', 'done_old')")
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 180, "overall_concurrency": 2,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 200.0, "total_runs": 4, "children": []}],
    }
    remote = {"errors": [], "runs": [
        {"relative_path": "active_a", "start_hint": now - 100, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 100.0, "event_time": now}},
        {"relative_path": "active_b", "start_hint": now - 100, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 100.0, "event_time": now}},
        {"relative_path": "current_done", "start_hint": now - 100, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 200.0, "event_time": now}},
    ]}
    summary, _ = _make_report(conn, {"id": "task"}, {}, config, remote)
    group = summary["groups"][0]
    assert group["etaSeconds"] is not None and 290 < group["etaSeconds"] < 310
    # Two slots: the queued 100-second run begins after either active run,
    # producing roughly 200 seconds instead of the category's 300-second sum.
    assert summary["overallEtaSeconds"] is not None and 190 < summary["overallEtaSeconds"] < 210
    assert summary["overallEtaSeconds"] < group["etaSeconds"]


def test_new_running_run_uses_group_history_as_a_queued_estimate() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO tpm_reports VALUES ('historical', ?, 1)", (_time_iso(now - 120),))
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'old_done', 'group', 200, 100, 'completed', 'historical')")
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 180, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 200.0, "total_runs": 2, "children": []}],
    }
    remote = {"errors": [], "runs": [
        {"relative_path": "just_started", "start_hint": now - 5, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 5.0, "event_time": now}},
        {"relative_path": "current_done", "start_hint": now - 100, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 200.0, "event_time": now}},
    ]}
    summary, runs = _make_report(conn, {"id": "task"}, {}, config, remote)
    just_started = next(run for run in runs if run["runKey"] == "just_started")
    assert just_started["status"] == "running"
    assert just_started["etaSeconds"] is None
    assert just_started["estimateAsQueued"] is True
    group = summary["groups"][0]
    assert group["provisionalQueuedRuns"] == 1
    assert group["etaSeconds"] is not None and 95 < group["etaSeconds"] < 105
    assert summary["overallEtaSeconds"] is not None and 95 < summary["overallEtaSeconds"] < 105


def test_running_estimate_supplies_duration_before_first_completion() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO tpm_reports VALUES ('old', ?, 1)", (_time_iso(now - 50),))
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'running', 'group', 0, NULL, 'running', 'old')")
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 180, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 200.0, "total_runs": 2, "children": []}],
    }
    remote = {"errors": [], "runs": [{"relative_path": "running", "start_hint": now - 50, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 100.0, "event_time": now}}]}
    summary, _ = _make_report(conn, {"id": "task"}, {}, config, remote)
    group = summary["groups"][0]
    # The active run has roughly 50 seconds left after 50 seconds elapsed, so
    # it supplies a roughly 100-second full-duration estimate for the queued run.
    assert group["durationSource"] == "running_estimate"
    assert group["medianDurationSeconds"] is not None and 95 < group["medianDurationSeconds"] < 105
    assert group["etaSeconds"] is not None and 145 < group["etaSeconds"] < 155
    assert summary["overallEtaSeconds"] is not None and 145 < summary["overallEtaSeconds"] < 155


def test_stalled_and_missing_tag_runs_use_group_history_as_queued_estimates() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    now = time.time()
    conn.execute("INSERT INTO tpm_reports VALUES ('historical', ?, 1)", (_time_iso(now - 120),))
    conn.execute("INSERT INTO tpm_run_samples VALUES ('task', 'old_done', 'group', 200, 100, 'completed', 'historical')")
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 60, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 200.0, "total_runs": 3, "children": []}],
    }
    remote = {"errors": [], "runs": [
        {"relative_path": "stalled", "start_hint": now - 300, "last_file_mtime": now - 120, "event_file_count": 1, "latest": {"progress": 20.0, "event_time": now - 120}},
        {"relative_path": "missing_tag", "start_hint": now - 5, "last_file_mtime": now, "event_file_count": 1, "latest": None},
        {"relative_path": "current_done", "start_hint": now - 100, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 200.0, "event_time": now}},
    ]}
    summary, runs = _make_report(conn, {"id": "task"}, {}, config, remote)
    assert {run["status"] for run in runs if run["runKey"] != "current_done"} == {"stalled", "waiting"}
    assert all(run["estimateAsQueued"] for run in runs if run["runKey"] != "current_done")
    group = summary["groups"][0]
    assert group["provisionalQueuedRuns"] == 2
    assert group["etaSeconds"] is not None and 195 < group["etaSeconds"] < 205
    assert summary["overallEtaSeconds"] is not None and 195 < summary["overallEtaSeconds"] < 205


def test_completed_group_without_duration_history_does_not_cast_none_to_float() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 180, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 100.0, "total_runs": 1, "children": []}],
    }
    remote = {"errors": [], "runs": [{"relative_path": "done", "event_file_count": 1, "latest": {"progress": 100.0, "event_time": time.time()}}]}
    summary, _ = _make_report(conn, {"id": "task"}, {}, config, remote)
    assert summary["overallEtaSeconds"] == 0.0


def test_unmodified_incomplete_log_is_marked_stalled() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    config = {
        "tensorboard_root": "/logs", "progress_tag": "train/step", "progress_mode": "event_step",
        "tail_bytes": 4096, "report_interval_seconds": 60, "rate_report_count": 5,
        "stale_after_seconds": 60, "overall_concurrency": 1,
        "groups": [{"name": "group", "key": "group", "pattern": "*", "target_step": 100.0, "total_runs": 1, "children": []}],
    }
    remote = {"errors": [], "runs": [{"relative_path": "stale", "start_hint": time.time() - 300, "last_file_mtime": time.time() - 120, "event_file_count": 1, "latest": {"progress": 10.0, "event_time": time.time() - 120}}]}
    _, runs = _make_report(conn, {"id": "task"}, {}, config, remote)
    assert runs[0]["status"] == "stalled"
    assert "日志文件" in runs[0]["error"]


def test_file_cache_is_invalidated_when_collection_semantics_change() -> None:
    config = {"tensorboard_root": "/logs", "progress_tag": "step", "progress_mode": "event_step", "tail_bytes": 4096}
    assert _same_collection_config(json.dumps(config), config)
    assert not _same_collection_config(json.dumps(config), {**config, "progress_tag": "eval/step"})


def test_raw_yaml_change_invalidates_all_history_and_report_history_is_bounded() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_tasks (id TEXT, last_report_at TEXT, last_config_json TEXT,
        last_yaml_hash TEXT, last_config_error TEXT, updated_at TEXT);
      CREATE TABLE tpm_reports (id TEXT, task_id TEXT, reported_at TEXT);
      CREATE TABLE tpm_run_samples (task_id TEXT, report_id TEXT);
      CREATE TABLE tpm_event_files (task_id TEXT, path TEXT);
    """)
    conn.execute("INSERT INTO tpm_tasks VALUES ('task', 'old', '{\"x\":1}', 'old-hash', '', 'old')")
    base = time.time()
    for index in range(7):
        report_id = f"r{index}"
        conn.execute("INSERT INTO tpm_reports VALUES (?, 'task', ?)", (report_id, _time_iso(base + index)))
        conn.execute("INSERT INTO tpm_run_samples VALUES ('task', ?)", (report_id,))
    _trim_report_history(conn, "task", rate_report_count=3)
    assert conn.execute("SELECT COUNT(*) FROM tpm_reports").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM tpm_run_samples").fetchone()[0] == 6
    conn.execute("INSERT INTO tpm_event_files VALUES ('task', '/logs/events')")
    original = "tensorboard_root: /logs\nprogress_tag: step\n"
    changed_only_in_comment = "# changed comment\ntensorboard_root: /logs\nprogress_tag: step\n"
    assert _yaml_hash(original) != _yaml_hash(changed_only_in_comment)
    _invalidate_task_history(conn, "task", _yaml_hash(changed_only_in_comment))
    assert conn.execute("SELECT COUNT(*) FROM tpm_reports").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tpm_run_samples").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tpm_event_files").fetchone()[0] == 0
    task = conn.execute("SELECT * FROM tpm_tasks WHERE id='task'").fetchone()
    assert task["last_report_at"] is None
    assert task["last_yaml_hash"] == _yaml_hash(changed_only_in_comment)


def test_nested_groups_assign_runs_to_deepest_match_and_fix_invalid_parent_total() -> None:
    config = _parse_config("""
tensorboard_root: /logs
progress_tag: step
overall_concurrency: 1
groups:
  - name: abc
    pattern: "abc-*"
    target_step: 100
    total_runs: 1
    children:
      - name: cde
        pattern: "*cde*"
        target_step: 50
        total_runs: 2
        children: []
""")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_reports (id TEXT, reported_at TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (task_id TEXT, run_key TEXT, group_name TEXT,
        progress REAL, duration_seconds REAL, status TEXT, report_id TEXT);
    """)
    now = time.time()
    remote = {"errors": [], "runs": [
        {"relative_path": "abc-cde", "start_hint": now - 20, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 10.0, "event_time": now}},
        {"relative_path": "abc-qwe", "start_hint": now - 20, "last_file_mtime": now, "event_file_count": 1, "latest": {"progress": 10.0, "event_time": now}},
    ]}
    summary, runs = _make_report(conn, {"id": "task"}, {}, config, remote)
    assert next(run for run in runs if run["relativePath"] == "abc-cde")["groupName"] == "abc/cde"
    assert next(run for run in runs if run["relativePath"] == "abc-qwe")["groupName"] == "abc"
    parent = next(group for group in summary["groups"] if group["name"] == "abc")
    assert parent["effectiveTotalRuns"] == 2
    assert "小于子群总数" in parent["reason"]


def _time_iso(value: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc).isoformat()
