from __future__ import annotations

import base64
import json
import shlex
import struct
import subprocess
import sys
import re
import sqlite3
import time
from pathlib import Path

from tools.tensorboard_progress_monitor.backend.service import REMOTE_COLLECTOR_SOURCE, _child_color_group_param, _color_key_regex, _conda_python_runner, _find_group, _find_group_path, _glob_regex, _group_path_color_regex, _group_path_regex, _group_patterns, _invalidate_task_history, _layer_tb_url_params, _make_report, _match_group, _merge_tb_url_params, _parse_config, _previous_file_states, _regex_param, _remote_collector_command, _same_collection_config, _serialize_extra_params, _tb_environment_configured, _trim_report_history, _validate_remote_yaml_path, _validate_tb_environment, _yaml_hash


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
    script = root / "tpm_remote_collector.py"
    script.write_text(REMOTE_COLLECTOR_SOURCE, encoding="utf-8")
    completed = subprocess.run(shlex.split(_remote_collector_command(sys.executable, str(script))), input=request, text=True, capture_output=True, check=False)
    assert completed.stderr == ""
    assert completed.returncode == 0
    return json.loads(completed.stdout)


def test_remote_collector_command_uses_a_short_script_invocation() -> None:
    command = _remote_collector_command("python3", "/tmp/collector.py")
    assert command == "python3 /tmp/collector.py --request-stdin-base64"


def test_remote_collector_large_file_cache_is_streamed_not_put_in_argv(tmp_path: Path) -> None:
    # This request is intentionally larger than the conservative 128 KiB
    # command-line limit found on some remote shells.
    previous = {
        f"very/deep/run_{index:05d}/events.out.tfevents.1700000000.host": {"size": index, "mtime_ns": index}
        for index in range(5000)
    }
    assert len(json.dumps(previous)) > 128 * 1024
    assert _collect(tmp_path, "event_step", previous)["runs"] == []


def test_file_cache_retries_runs_that_have_never_produced_progress() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE tpm_event_files (task_id TEXT, path TEXT, size INTEGER, mtime_ns INTEGER, last_seen_at TEXT);
      CREATE TABLE tpm_reports (id TEXT, task_id TEXT, success INTEGER);
      CREATE TABLE tpm_run_samples (report_id TEXT, task_id TEXT, relative_path TEXT, progress REAL);
    """)
    conn.executemany(
        "INSERT INTO tpm_event_files VALUES ('task',?,?,?, '')",
        [("known/events.out.tfevents.1", 10, 1), ("missing/events.out.tfevents.1", 10, 1)],
    )
    conn.execute("INSERT INTO tpm_reports VALUES ('report', 'task', 1)")
    conn.execute("INSERT INTO tpm_run_samples VALUES ('report', 'task', 'known', 12)")
    conn.execute("INSERT INTO tpm_run_samples VALUES ('report', 'task', 'missing', NULL)")
    assert _previous_file_states(conn, "task") == {"known/events.out.tfevents.1": {"size": 10, "mtime_ns": 1}}


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


def test_tb_group_star_wildcards_use_tensorboard_compatible_regex() -> None:
    patterns = ["policy_random_rebalanced*config_new_813", "*td3_bc*"]
    assert _glob_regex(patterns[0]) == "policy_random_rebalanced.*config_new_813"
    assert _glob_regex(patterns[1]) == ".*td3_bc.*"
    assert _glob_regex(".*td3_bc.*") == ".*td3_bc.*"
    assert _regex_param(patterns) == "policy_random_rebalanced.*config_new_813|.*td3_bc.*"


def test_remote_yaml_path_rejects_empty_or_nul_paths() -> None:
    assert _validate_remote_yaml_path(" /data/monitor.yaml ") == "/data/monitor.yaml"
    for invalid in ("", "\x00bad.yaml"):
        try:
            _validate_remote_yaml_path(invalid)
        except Exception as exc:
            assert "路径" in str(exc)
        else:
            raise AssertionError("expected invalid remote YAML path")


def test_tb_environment_is_optional_for_collection_but_must_be_complete_for_tb() -> None:
    assert _validate_tb_environment({"tbPythonMode": "conda", "tbCondaBasePath": "", "tbCondaEnv": "", "tbPythonPath": ""}) == ("conda", "", "", "")
    assert _validate_tb_environment({"tbPythonMode": "conda", "tbCondaBasePath": "/opt/conda", "tbCondaEnv": "", "tbPythonPath": ""}) == ("conda", "/opt/conda", "", "")
    assert _tb_environment_configured({"tbPythonMode": "conda", "tbCondaBasePath": "/opt/conda", "tbCondaEnv": "", "tbPythonPath": ""})
    assert _tb_environment_configured({"tbPythonMode": "conda", "tbCondaBasePath": "/opt/conda", "tbCondaEnv": "tb", "tbPythonPath": ""})
    assert _tb_environment_configured({"tbPythonMode": "path", "tbCondaBasePath": "", "tbCondaEnv": "", "tbPythonPath": "/venv/bin/python"})


def test_empty_conda_environment_activates_the_conda_base() -> None:
    assert "conda activate /opt/conda" in _conda_python_runner({"tb_conda_base_path": "/opt/conda", "tb_conda_env": ""})
    assert "conda activate tb" in _conda_python_runner({"tb_conda_base_path": "/opt/conda", "tb_conda_env": "tb"})


def test_tb_group_filters_cover_descendants_and_group_children_for_color() -> None:
    config = _parse_config("""
tensorboard_root: /logs
progress_tag: train/step
groups:
  - name: parent
    pattern: parent/*
    target_step: 1
    children:
      - name: first
        pattern: parent/first/*
        target_step: 1
        children: []
      - name: second
        pattern: parent/second/*
        target_step: 1
        children: []
""")
    parent_path = _find_group_path(config["groups"], "parent")
    first_path = _find_group_path(config["groups"], "parent/first")
    assert parent_path is not None and first_path is not None
    assert _group_path_regex(parent_path) == "(?=.*parent/.*).*"
    assert _group_path_regex(first_path) == "(?=.*parent/.*)(?=.*parent/first/.*).*"
    assert _color_key_regex("*/first/*") == ".*(/first/).*"
    assert _group_path_color_regex([parent_path[0], {**first_path[1], "pattern": "*/first/*"}]) == "(?=.*parent/.*)(?=.*(/first/).*).*"
    color_regex = _child_color_group_param(parent_path)
    assert color_regex == "regex:(?=.*parent/.*)(?=(parent/first/).*).*|(?=.*parent/.*)(?=(parent/second/).*).*"
    # TensorBoard groups by capture contents.  Both first runs have the same
    # key even though their remaining run names differ.
    compiled = re.compile(color_regex.removeprefix("regex:"))
    first_iql = compiled.match("parent/first/iql/seed_1")
    first_bc = compiled.match("parent/first/bc/seed_2")
    second = compiled.match("parent/second/iql/seed_1")
    assert first_iql is not None and first_bc is not None and second is not None
    assert first_iql.groups() == first_bc.groups() == ("parent/first/", None)
    assert second.groups() == (None, "parent/second/")
    assert _child_color_group_param(first_path) is None


def test_tb_extra_params_require_named_url_groups() -> None:
    assert json.loads(_serialize_extra_params([{"label": "平滑", "params": "?tagFilter=d4rl&smoothing=0.79#timeseries"}]))[0]["label"] == "平滑"
    try:
        _serialize_extra_params([{"label": "", "params": "?tagFilter=d4rl"}])
    except Exception as exc:
        assert "名称" in str(exc)
    else:
        raise AssertionError("expected named parameter validation")


def test_tb_url_params_preserve_user_settings_but_enforce_group_controls() -> None:
    query, fragment = _merge_tb_url_params(
        "?tagFilter=d4rl&smoothing=0.79&runFilter=old&runColorGroup=old#timeseries",
        "selected", "child_a|child_b",
    )
    values = dict(__import__("urllib.parse").parse.parse_qsl(query))
    assert values == {"tagFilter": "d4rl", "smoothing": "0.79", "runFilter": "selected", "runColorGroup": "child_a|child_b"}
    assert fragment == "timeseries"


def test_tb_color_group_uses_tensorboard_regex_selector_but_filter_is_raw_regex() -> None:
    query, _ = _merge_tb_url_params("", "(?=.*parent/.*).*", "regex:((?=.*parent/.*)(?=.*child/.*).*)")
    values = dict(__import__("urllib.parse").parse.parse_qsl(query))
    assert values["runFilter"] == "(?=.*parent/.*).*"
    assert values["runColorGroup"] == "regex:((?=.*parent/.*)(?=.*child/.*).*)"


def test_yaml_tb_custom_params_override_report_defaults() -> None:
    layered = _layer_tb_url_params("?tagFilter=default&smoothing=0.6#scalars", "?tagFilter=d4rl#timeseries")
    parsed = __import__("urllib.parse").parse.urlsplit(layered)
    assert dict(__import__("urllib.parse").parse.parse_qsl(parsed.query)) == {"smoothing": "0.6", "tagFilter": "d4rl"}
    assert parsed.fragment == "timeseries"
    assert _parse_config("tensorboard_root: /logs\nprogress_tag: step\ntb_custom_params: '?smoothing=0.79'\ngroups: []\n")["tb_custom_params"] == "?smoothing=0.79"


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
    include_unmatched_children: true
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


def test_parent_only_runs_are_excluded_by_default_but_can_be_included() -> None:
    raw = """
tensorboard_root: /logs
progress_tag: step
groups:
  - name: parent
    pattern: "parent-*"
    target_step: 100
    children:
      - name: child
        pattern: "*child*"
        target_step: 100
        children: []
"""
    config = _parse_config(raw)
    assert _match_group("parent-other", config["groups"]) is None
    assert _match_group("parent-child", config["groups"])["key"] == "parent/child"

    enabled = _parse_config(raw.replace("    children:\n", "    include_unmatched_children: true\n    children:\n", 1))
    assert _match_group("parent-other", enabled["groups"])["key"] == "parent"


def _time_iso(value: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc).isoformat()
