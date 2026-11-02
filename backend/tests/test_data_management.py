from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services import data_management
from backend.app.services.data_management import DataCategory


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _patch_user_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(data_management, "get_user_tool_connection", lambda _user, _tool: _connect(path))
    monkeypatch.setattr(data_management, "get_user_tool_db_path", lambda _user, _tool: path)
    monkeypatch.setattr(data_management, "list_user_tool_dbs", lambda _tool: [("test-user", path)])


def test_unbounded_delete_removes_time_based_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "user.db"
    with _connect(db_path) as connection:
        connection.execute("CREATE TABLE events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO events VALUES (?, ?)",
            [("old", "2025-01-01T00:00:00+00:00"), ("new", "2026-01-01T00:00:00+00:00")],
        )

    tool_id = "test_unbounded_cleanup"
    data_management.register_tool_categories(
        tool_id,
        [DataCategory("events", ["events"], "created_at", "事件")],
    )
    _patch_user_database(monkeypatch, db_path)

    result = data_management.delete_data(tool_id, user_id="test-user", category="events")

    assert result["deleted"] == {"events": {"events": 2}}
    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_admin_cleanup_aggregates_all_user_databases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_paths = {user_id: tmp_path / f"{user_id}.db" for user_id in ("user-a", "user-b")}
    for user_id, db_path in db_paths.items():
        with _connect(db_path) as connection:
            connection.execute("CREATE TABLE events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
            connection.execute(
                "INSERT INTO events VALUES (?, '2026-01-01T00:00:00+00:00')",
                (f"event-{user_id}",),
            )

    tool_id = "test_all_user_cleanup"
    data_management.register_tool_categories(
        tool_id,
        [DataCategory("events", ["events"], "created_at", "事件")],
    )
    monkeypatch.setattr(
        data_management,
        "get_user_tool_connection",
        lambda user_id, _tool: _connect(db_paths[user_id]),
    )
    monkeypatch.setattr(
        data_management,
        "get_user_tool_db_path",
        lambda user_id, _tool: db_paths[user_id],
    )
    monkeypatch.setattr(
        data_management,
        "list_user_tool_dbs",
        lambda _tool: list(db_paths.items()),
    )

    assert data_management.count_data(tool_id, category="events") == 2
    result = data_management.delete_data(tool_id, category="events")

    assert result["deleted"] == {"events": {"events": 2}}
    for db_path in db_paths.values():
        with _connect(db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_date_range_uses_each_tables_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "user.db"
    with _connect(db_path) as connection:
        connection.execute("CREATE TABLE refreshes (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, requested_at TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO refreshes VALUES (?, ?)",
            [("before", "2025-01-01T00:00:00+00:00"), ("inside", "2026-06-01T00:00:00+00:00")],
        )
        connection.executemany(
            "INSERT INTO requests VALUES (?, ?)",
            [("inside", "2026-06-01T00:00:00+00:00"), ("after", "2027-01-01T00:00:00+00:00")],
        )

    tool_id = "test_per_table_timestamps"
    data_management.register_tool_categories(
        tool_id,
        [
            DataCategory(
                "history",
                ["refreshes", "requests"],
                "created_at",
                "历史",
                table_time_columns={"requests": "requested_at"},
            )
        ],
    )
    _patch_user_database(monkeypatch, db_path)

    result = data_management.delete_data(
        tool_id,
        user_id="test-user",
        category="history",
        after_date="2026-01-01T00:00:00+00:00",
        before_date="2027-01-01T00:00:00+00:00",
    )

    assert result["deleted"] == {"history": {"refreshes": 1, "requests": 1}}
    with _connect(db_path) as connection:
        assert connection.execute("SELECT id FROM refreshes").fetchone()[0] == "before"
        assert connection.execute("SELECT id FROM requests").fetchone()[0] == "after"


def test_config_cleanup_cascades_in_fk_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "user.db"
    with _connect(db_path) as connection:
        connection.execute("CREATE TABLE configs (id TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE samples (id TEXT PRIMARY KEY, config_id TEXT NOT NULL, created_at TEXT NOT NULL, "
            "FOREIGN KEY(config_id) REFERENCES configs(id))"
        )
        connection.execute("INSERT INTO configs VALUES ('config-1')")
        connection.execute("INSERT INTO samples VALUES ('sample-1', 'config-1', '2026-01-01T00:00:00+00:00')")

    tool_id = "test_cascade_cleanup"
    data_management.register_tool_categories(
        tool_id,
        [
            DataCategory("config", ["configs"], None, "配置", cascade_categories=["samples"]),
            DataCategory("samples", ["samples"], "created_at", "采样"),
        ],
    )
    _patch_user_database(monkeypatch, db_path)

    assert data_management.count_data(tool_id, user_id="test-user", category="config") == 2
    result = data_management.delete_data(tool_id, user_id="test-user", category="config")

    assert result["deleted"] == {"samples": {"samples": 1}, "config": {"configs": 1}}
    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM configs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0


def test_cleanup_rolls_back_when_a_dependency_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "user.db"
    with _connect(db_path) as connection:
        connection.execute("CREATE TABLE configs (id TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE samples (id TEXT PRIMARY KEY, config_id TEXT NOT NULL, created_at TEXT NOT NULL, "
            "FOREIGN KEY(config_id) REFERENCES configs(id))"
        )
        connection.execute(
            "CREATE TABLE unregistered_refs (id TEXT PRIMARY KEY, config_id TEXT NOT NULL, "
            "FOREIGN KEY(config_id) REFERENCES configs(id))"
        )
        connection.execute("INSERT INTO configs VALUES ('config-1')")
        connection.execute("INSERT INTO samples VALUES ('sample-1', 'config-1', '2026-01-01T00:00:00+00:00')")
        connection.execute("INSERT INTO unregistered_refs VALUES ('ref-1', 'config-1')")

    tool_id = "test_cleanup_rollback"
    data_management.register_tool_categories(
        tool_id,
        [
            DataCategory("config", ["configs"], None, "配置", cascade_categories=["samples"]),
            DataCategory("samples", ["samples"], "created_at", "采样"),
        ],
    )
    _patch_user_database(monkeypatch, db_path)

    with pytest.raises(sqlite3.IntegrityError):
        data_management.delete_data(tool_id, user_id="test-user", category="config")

    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM configs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_platform_usage_and_cleanup_work_without_user_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    platform_path = tmp_path / "platform.db"
    missing_user_path = tmp_path / "missing-user.db"
    with _connect(platform_path) as connection:
        connection.execute(
            "CREATE TABLE public_items (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO public_items VALUES (?, ?, ?)",
            [
                ("a", "user-a", "2026-01-01T00:00:00+00:00"),
                ("b", "user-b", "2026-01-01T00:00:00+00:00"),
            ],
        )

    tool_id = "test_platform_cleanup"
    data_management.register_tool_categories(
        tool_id,
        [
            DataCategory(
                "public",
                ["public_items"],
                "created_at",
                "公开数据",
                storage="platform_db",
                user_id_column="user_id",
            )
        ],
    )
    monkeypatch.setattr(data_management, "get_connection", lambda: _connect(platform_path))
    monkeypatch.setattr(data_management, "list_user_tool_dbs", lambda _tool: [])
    monkeypatch.setattr(data_management, "get_user_tool_db_path", lambda _user, _tool: missing_user_path)
    monkeypatch.setattr(
        data_management,
        "get_settings",
        lambda: SimpleNamespace(platform_db_path=platform_path),
    )

    all_usage = data_management.get_data_usage_by_category(tool_id)
    user_usage = data_management.get_data_usage_by_category(tool_id, user_id="user-a")
    assert all_usage[0]["totalRows"] == 2
    assert user_usage[0]["totalRows"] == 1
    assert data_management.count_data(tool_id, user_id="user-a", category="public") == 1

    result = data_management.delete_data(tool_id, user_id="user-a", category="public")
    assert result["deleted"] == {"public": {"public_items": 1}}
    with _connect(platform_path) as connection:
        assert connection.execute("SELECT user_id FROM public_items").fetchone()[0] == "user-b"

    global_result = data_management.delete_data(tool_id, category="public")
    assert global_result["deleted"] == {"public": {"public_items": 1}}
