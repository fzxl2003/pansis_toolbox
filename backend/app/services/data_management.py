"""Data management framework: category registry + time/category-based deletion.

Each tool registers its data categories at import time.  A category describes
a logical group of database tables, the timestamp column used for time-based
operations (or ``None`` for configuration data that cannot be pruned by time),
and whether the data lives in a per-user per-tool database or in the shared
platform database.

The framework provides unified APIs for:
- Querying categories (``get_tool_categories``)
- Deleting data by tool / category / time-range in any combination
  (``delete_data``)
- Estimating per-category storage usage (``get_data_usage_by_category``)
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import (
    get_connection,
    get_user_tool_db_path,
    get_user_tool_connection,
    list_user_tool_dbs,
)


# ============================================================
# Category registry
# ============================================================

@dataclass(frozen=True)
class DataCategory:
    """A logical group of database tables belonging to a tool.

    Attributes:
        name: Short identifier, e.g. ``"samples"``, ``"config"``.
        tables: Database table names in this category.
        time_column: Column name holding the row timestamp.  ``None`` marks
            the category as configuration data that cannot be pruned by time.
        description: Human-readable description shown in the settings UI.
        storage: ``"user_tool_db"`` for per-user databases, ``"platform_db"``
            for the shared platform database.
        table_time_columns: Optional per-table timestamp overrides.
        cascade_categories: Categories that must be removed before this one
            when the category is deleted without a time filter.
    """

    name: str
    tables: list[str]
    time_column: str | None
    description: str
    storage: str = "user_tool_db"  # "user_tool_db" | "platform_db"
    # Platform-owned rows normally belong to the entire installation.  Tools
    # that keep a small public lookup index can opt into user-scoped cleanup.
    user_id_column: str | None = None
    # Some logical categories group tables that use different timestamp
    # columns.  Entries here override ``time_column`` for a specific table.
    table_time_columns: dict[str, str | None] = field(default_factory=dict)
    # Deleting this category in full also deletes these dependent categories.
    # This is intentionally explicit so cleanup never broadens its scope based
    # on an unrelated foreign key in the shared platform database.
    cascade_categories: list[str] = field(default_factory=list)


# Global registry: tool_id -> list of categories
_TOOL_CATEGORIES: dict[str, list[DataCategory]] = {}


def register_tool_categories(tool_id: str, categories: list[DataCategory]) -> None:
    """Register data categories for a tool.  Called at tool import time."""
    _TOOL_CATEGORIES[tool_id] = list(categories)


def get_tool_categories(tool_id: str) -> list[DataCategory]:
    """Return the registered categories for *tool_id* (empty if none)."""
    return list(_TOOL_CATEGORIES.get(tool_id, []))


def _table_time_column(category: DataCategory, table: str) -> str | None:
    return category.table_time_columns.get(table, category.time_column)


def _expand_cascade_categories(
    all_categories: list[DataCategory],
    selected_categories: list[DataCategory],
) -> list[DataCategory]:
    """Expand explicit cleanup dependencies while preserving registry order."""
    by_name = {category.name: category for category in all_categories}
    selected_names = {category.name for category in selected_categories}
    pending = list(selected_categories)
    while pending:
        current = pending.pop()
        for name in current.cascade_categories:
            if name in selected_names or name not in by_name:
                continue
            selected_names.add(name)
            pending.append(by_name[name])
    return [category for category in all_categories if category.name in selected_names]


def all_registered_tools() -> list[str]:
    """Return tool_ids that have registered categories."""
    return sorted(_TOOL_CATEGORIES.keys())


# ============================================================
# Time helpers
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_before_date(days: int | None) -> str | None:
    """Return an ISO timestamp *days* ago, or ``None`` for no time filter."""
    if days is None or days <= 0:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def compute_date_bounds(
    start_date: str | None,
    end_date: str | None,
) -> tuple[str | None, str | None]:
    """Convert a day-granularity date range into ISO timestamp bounds.

    Parameters:
        start_date: Inclusive start day (``YYYY-MM-DD``).  Converted to the
            start of that day (00:00 UTC).
        end_date: Inclusive end day (``YYYY-MM-DD``).  Converted to the start
            of the *next* day (exclusive upper bound).

    Returns ``(after_date, before_date)`` where either may be ``None``.
    """
    after_date: str | None = None
    before_date: str | None = None
    if start_date:
        try:
            d = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            after_date = d.isoformat()
        except ValueError:
            after_date = None
    if end_date:
        try:
            d = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            before_date = d.isoformat()
        except ValueError:
            before_date = None
    return after_date, before_date


# ============================================================
# Data deletion
# ============================================================

def delete_data(
    tool_id: str,
    user_id: str | None = None,
    category: str | None = None,
    before_date: str | None = None,
    after_date: str | None = None,
) -> dict[str, Any]:
    """Delete data for a tool, optionally filtered by category and/or time.

    Parameters:
        tool_id: The tool whose data should be deleted.
        user_id: For ``user_tool_db`` storage, the user whose database to
            operate on.  If ``None``, the operation applies to **all** users'
            databases for the tool.
        category: If given, only delete tables in this category.  If ``None``,
            delete tables across **all** categories.
        before_date: ISO timestamp (exclusive upper bound).  If given, only
            delete rows whose ``time_column`` value is older than this.
        after_date: ISO timestamp (inclusive lower bound).  If given, only
            delete rows whose ``time_column`` value is newer than or equal to
            this.  Categories without a ``time_column`` are skipped when either
            bound is set.

    Returns a summary dict with deleted row counts per table.
    """
    all_categories = get_tool_categories(tool_id)
    if not all_categories:
        return {"toolId": tool_id, "deleted": {}, "message": "未注册数据分类"}

    # Filter categories
    categories = all_categories
    if category is not None:
        categories = [c for c in categories if c.name == category]
        if not categories:
            return {"toolId": tool_id, "deleted": {}, "message": f"分类 {category} 不存在"}

    # If a time bound is set, skip non-time-based categories
    has_time_filter = before_date is not None or after_date is not None
    effective_categories = categories
    if has_time_filter:
        effective_categories = [c for c in categories if c.time_column is not None]
        if not effective_categories:
            return {
                "toolId": tool_id,
                "deleted": {},
                "message": "所选分类均为配置数据，无法按时间删除",
            }
    else:
        effective_categories = _expand_cascade_categories(all_categories, effective_categories)

    deleted: dict[str, dict[str, int]] = {}

    # Determine which storage(s) to operate on
    user_tool_cats = [c for c in effective_categories if c.storage == "user_tool_db"]
    platform_cats = [c for c in effective_categories if c.storage == "platform_db"]

    if user_tool_cats:
        if user_id is not None:
            _merge_deleted_counts(
                deleted,
                _delete_user_tool_data(tool_id, user_id, user_tool_cats, before_date, after_date),
            )
        else:
            # All users
            for uid, _path in list_user_tool_dbs(tool_id):
                _merge_deleted_counts(
                    deleted,
                    _delete_user_tool_data(tool_id, uid, user_tool_cats, before_date, after_date),
                )

    if platform_cats:
        _merge_deleted_counts(
            deleted,
            _delete_platform_data(tool_id, platform_cats, before_date, after_date, user_id),
        )

    return {"toolId": tool_id, "deleted": deleted}


def _merge_deleted_counts(
    target: dict[str, dict[str, int]],
    source: dict[str, dict[str, int]],
) -> None:
    for category, tables in source.items():
        category_counts = target.setdefault(category, {})
        for table, count in tables.items():
            category_counts[table] = category_counts.get(table, 0) + count


def _delete_user_tool_data(
    tool_id: str,
    user_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
) -> dict[str, dict[str, int]]:
    """Delete rows from a single user's per-tool database."""
    result: dict[str, dict[str, int]] = {}
    if not get_user_tool_db_path(user_id, tool_id).exists():
        return result
    conn = get_user_tool_connection(user_id, tool_id)
    try:
        for cat, table in _ordered_category_tables(conn, categories):
            count = _delete_rows(
                conn,
                table,
                _table_time_column(cat, table),
                before_date,
                after_date,
            )
            if count > 0:
                result.setdefault(cat.name, {})[table] = count
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def _delete_platform_data(
    tool_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
    user_id: str | None = None,
) -> dict[str, dict[str, int]]:
    """Delete rows from the shared platform database."""
    result: dict[str, dict[str, int]] = {}
    conn = get_connection()
    try:
        for cat, table in _ordered_category_tables(conn, categories):
            count = _delete_rows(
                conn,
                table,
                _table_time_column(cat, table),
                before_date,
                after_date,
                user_id if cat.user_id_column else None,
                cat.user_id_column,
            )
            if count > 0:
                result.setdefault(cat.name, {})[table] = count
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def _ordered_category_tables(
    conn: sqlite3.Connection,
    categories: list[DataCategory],
) -> list[tuple[DataCategory, str]]:
    """Return registered tables in child-before-parent foreign-key order."""
    table_categories: dict[str, DataCategory] = {}
    declared_order: list[str] = []
    for category in categories:
        for table in category.tables:
            if table in table_categories:
                continue
            table_categories[table] = category
            declared_order.append(table)

    selected_tables = set(declared_order)
    edges: dict[str, set[str]] = {table: set() for table in declared_order}
    indegree: dict[str, int] = {table: 0 for table in declared_order}
    for child in declared_order:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (child,),
        ).fetchone()
        if exists is None:
            continue
        for foreign_key in conn.execute(f'PRAGMA foreign_key_list("{child}")').fetchall():
            parent = foreign_key["table"]
            if parent not in selected_tables or parent == child or parent in edges[child]:
                continue
            edges[child].add(parent)
            indegree[parent] += 1

    ready = [table for table in declared_order if indegree[table] == 0]
    ordered: list[str] = []
    while ready:
        child = ready.pop(0)
        ordered.append(child)
        for parent in edges[child]:
            indegree[parent] -= 1
            if indegree[parent] == 0:
                ready.append(parent)

    # Cycles are not expected in registered tool schemas. Keep their declared
    # order so SQLite can reject the transaction without leaving partial data.
    ordered.extend(table for table in declared_order if table not in ordered)
    return [(table_categories[table], table) for table in ordered]


def _delete_rows(
    conn: sqlite3.Connection,
    table: str,
    time_column: str | None,
    before_date: str | None,
    after_date: str | None = None,
    user_id: str | None = None,
    user_id_column: str | None = None,
) -> int:
    """Delete rows from *table*, optionally filtered by a time range.

    Returns the number of deleted rows.
    """
    # Verify the table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return 0

    has_time_filter = before_date is not None or after_date is not None
    columns = {col["name"] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if user_id is not None and (not user_id_column or user_id_column not in columns):
        return 0
    if has_time_filter:
        if time_column is None:
            return 0
        if time_column not in columns:
            return 0
        clauses: list[str] = []
        params: list[str] = []
        if after_date is not None:
            clauses.append(f'"{time_column}" >= ?')
            params.append(after_date)
        if before_date is not None:
            clauses.append(f'"{time_column}" < ?')
            params.append(before_date)
        if user_id is not None:
            clauses.append(f'"{user_id_column}" = ?')
            params.append(user_id)
        cursor = conn.execute(f'DELETE FROM "{table}" WHERE {" AND ".join(clauses)}', params)
    else:
        if user_id is not None:
            cursor = conn.execute(f'DELETE FROM "{table}" WHERE "{user_id_column}" = ?', (user_id,))
        else:
            cursor = conn.execute(f'DELETE FROM "{table}"')
    return cursor.rowcount or 0


def _count_rows(
    conn: sqlite3.Connection,
    table: str,
    time_column: str | None,
    before_date: str | None,
    after_date: str | None = None,
    user_id: str | None = None,
    user_id_column: str | None = None,
) -> int:
    """Count rows from *table* that match the same time filter used by deletion.

    This mirrors :func:`_delete_rows` but performs a ``SELECT COUNT(*)``
    instead of a ``DELETE``, so the UI can preview how many rows would be
    removed before the user confirms.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return 0

    has_time_filter = before_date is not None or after_date is not None
    columns = {col["name"] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if user_id is not None and (not user_id_column or user_id_column not in columns):
        return 0
    if has_time_filter:
        if time_column is None:
            return 0
        if time_column not in columns:
            return 0
        clauses: list[str] = []
        params: list[str] = []
        if after_date is not None:
            clauses.append(f'"{time_column}" >= ?')
            params.append(after_date)
        if before_date is not None:
            clauses.append(f'"{time_column}" < ?')
            params.append(before_date)
        if user_id is not None:
            clauses.append(f'"{user_id_column}" = ?')
            params.append(user_id)
        count_row = conn.execute(
            f'SELECT COUNT(*) AS c FROM "{table}" WHERE {" AND ".join(clauses)}',
            params,
        ).fetchone()
    else:
        if user_id is not None:
            count_row = conn.execute(
                f'SELECT COUNT(*) AS c FROM "{table}" WHERE "{user_id_column}" = ?',
                (user_id,),
            ).fetchone()
        else:
            count_row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()
    return count_row["c"] if count_row else 0


def count_data(
    tool_id: str,
    user_id: str | None = None,
    category: str | None = None,
    before_date: str | None = None,
    after_date: str | None = None,
) -> int:
    """Count the rows that *would* be deleted by the same filters as :func:`delete_data`.

    Parameters mirror :func:`delete_data`.  Returns the total matching row count
    across all matching tables (and across all users when *user_id* is ``None``
    in admin mode).
    """
    all_categories = get_tool_categories(tool_id)
    if not all_categories:
        return 0

    categories = all_categories
    if category is not None:
        categories = [c for c in categories if c.name == category]
        if not categories:
            return 0

    has_time_filter = before_date is not None or after_date is not None
    if has_time_filter:
        categories = [c for c in categories if c.time_column is not None]
        if not categories:
            return 0
    else:
        categories = _expand_cascade_categories(all_categories, categories)

    total = 0
    user_tool_cats = [c for c in categories if c.storage == "user_tool_db"]
    platform_cats = [c for c in categories if c.storage == "platform_db"]

    if user_tool_cats:
        if user_id is not None:
            total += _count_user_tool_data(tool_id, user_id, user_tool_cats, before_date, after_date)
        else:
            for uid, _path in list_user_tool_dbs(tool_id):
                total += _count_user_tool_data(tool_id, uid, user_tool_cats, before_date, after_date)

    if platform_cats:
        total += _count_platform_data(tool_id, platform_cats, before_date, after_date, user_id)

    return total


def _count_user_tool_data(
    tool_id: str,
    user_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
) -> int:
    """Count matching rows from a single user's per-tool database."""
    total = 0
    if not get_user_tool_db_path(user_id, tool_id).exists():
        return 0
    conn = get_user_tool_connection(user_id, tool_id)
    try:
        for cat in categories:
            for table in cat.tables:
                total += _count_rows(
                    conn,
                    table,
                    _table_time_column(cat, table),
                    before_date,
                    after_date,
                )
    finally:
        conn.close()
    return total


def _count_platform_data(
    tool_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
    user_id: str | None = None,
) -> int:
    """Count matching rows from the shared platform database."""
    total = 0
    conn = get_connection()
    try:
        for cat in categories:
            for table in cat.tables:
                total += _count_rows(
                    conn,
                    table,
                    _table_time_column(cat, table),
                    before_date,
                    after_date,
                    user_id if cat.user_id_column else None,
                    cat.user_id_column,
                )
    finally:
        conn.close()
    return total


_space_reclaim_lock = Lock()
logger = logging.getLogger(__name__)


def reclaim_database_space(
    tool_ids: list[str], user_id: str | None, *, preview: bool,
) -> dict[str, int]:
    """Inspect or compact existing databases within the caller's authorized scope."""
    if not preview and not _space_reclaim_lock.acquire(blocking=False):
        raise ToolboxError("RECLAIM_IN_PROGRESS", "已有数据库空间回收任务在运行，请稍后再试", status_code=409)
    try:
        paths: set[Path] = set()
        for tool_id in tool_ids:
            categories = get_tool_categories(tool_id)
            if any(category.storage == "user_tool_db" for category in categories):
                paths.update(
                    path for owner_id, path in list_user_tool_dbs(tool_id)
                    if user_id is None or owner_id == user_id
                )
            if user_id is None and any(category.storage == "platform_db" for category in categories):
                paths.add(get_settings().platform_db_path)

        result = {"estimatedBytes": 0, "reclaimedBytes": 0, "compacted": 0, "failed": 0}
        for path in sorted(paths):
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + ("?mode=ro" if preview else "?mode=rw"), uri=True, timeout=1)) as db:
                    free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
                    page_size = db.execute("PRAGMA page_size").fetchone()[0]
                    result["estimatedBytes"] += free_pages * page_size
                    if preview or not free_pages:
                        continue
                    before = _database_file_bytes(path)
                    db.execute("VACUUM")
                    if db.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                        db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    result["compacted"] += 1
                    result["reclaimedBytes"] += max(0, before - _database_file_bytes(path))
            except (OSError, sqlite3.Error):
                result["failed"] += 1
                logger.exception("Could not reclaim database space for %s", path)
        return result
    finally:
        if not preview:
            _space_reclaim_lock.release()


def _database_file_bytes(path: Path) -> int:
    wal = path.with_name(path.name + "-wal")
    try:
        wal_bytes = wal.stat().st_size
    except FileNotFoundError:
        wal_bytes = 0
    return path.stat().st_size + wal_bytes


# ============================================================
# Data usage by category
# ============================================================

def get_data_usage_by_category(tool_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
    """Return per-category row counts and estimated bytes.

    When *user_id* is given, only that user's per-tool database is queried.
    When *user_id* is ``None`` (admin "all users" mode), row counts are
    aggregated across every existing per-user database for the tool, and
    ``dbBytes`` reflects the sum of all user DB file sizes.
    """
    categories = get_tool_categories(tool_id)
    if not categories:
        return []

    cat_totals: dict[str, int] = {c.name: 0 for c in categories}
    cat_tables: dict[str, dict[str, int]] = {c.name: {} for c in categories}
    cat_db_bytes: dict[str, int] = {c.name: 0 for c in categories}
    user_categories = [c for c in categories if c.storage == "user_tool_db"]
    platform_categories = [c for c in categories if c.storage == "platform_db"]

    # Aggregate per-user databases independently from the platform database.
    if user_id is not None:
        db_path = get_user_tool_db_path(user_id, tool_id)
        user_dbs: list[tuple[str, Path]] = [(user_id, db_path)] if db_path.exists() else []
    else:
        user_dbs = list_user_tool_dbs(tool_id)

    for uid, db_path in user_dbs:
        try:
            db_size = db_path.stat().st_size
        except OSError:
            db_size = 0
        for cat in user_categories:
            cat_db_bytes[cat.name] += db_size
        try:
            conn = get_user_tool_connection(uid, tool_id)
        except Exception:
            continue
        try:
            for cat in user_categories:
                for table in cat.tables:
                    row = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if row is None:
                        continue
                    count_row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()
                    count = count_row["c"] if count_row else 0
                    cat_totals[cat.name] += count
                    prev = cat_tables[cat.name].get(table, 0)
                    cat_tables[cat.name][table] = prev + count
        finally:
            conn.close()

    if platform_categories:
        platform_path = get_settings().platform_db_path
        try:
            platform_size = platform_path.stat().st_size
        except OSError:
            platform_size = 0
        for cat in platform_categories:
            cat_db_bytes[cat.name] = platform_size

        conn = get_connection()
        try:
            for cat in platform_categories:
                for table in cat.tables:
                    count = _count_rows(
                        conn,
                        table,
                        _table_time_column(cat, table),
                        None,
                        None,
                        user_id if cat.user_id_column else None,
                        cat.user_id_column,
                    )
                    if count <= 0:
                        continue
                    cat_totals[cat.name] += count
                    cat_tables[cat.name][table] = count
        finally:
            conn.close()

    result: list[dict[str, Any]] = []
    for cat in categories:
        table_details = [
            {"table": t, "rows": r}
            for t, r in cat_tables[cat.name].items()
        ]
        result.append({
            "category": cat.name,
            "description": cat.description,
            "timeColumn": cat.time_column,
            "totalRows": cat_totals[cat.name],
            "dbBytes": cat_db_bytes[cat.name],
            "tables": table_details,
        })
    return result


def _attach_db_size(db_path: Path, categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the total DB file size to the first category in the list."""
    try:
        size = db_path.stat().st_size
    except OSError:
        size = 0
    for cat in categories:
        cat["dbBytes"] = size
    return categories


# ============================================================
# Pruning (retention-based auto cleanup)
# ============================================================

def prune_tool_data(tool_id: str, user_id: str, retention_days: dict[str, int]) -> dict[str, Any]:
    """Prune time-based categories according to per-category retention days.

    ``retention_days`` maps category names to maximum age in days.  Categories
    not in the dict are left untouched.
    """
    categories = get_tool_categories(tool_id)
    deleted: dict[str, dict[str, int]] = {}
    for cat in categories:
        days = retention_days.get(cat.name)
        if days is None or days <= 0 or cat.time_column is None:
            continue
        before = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = delete_data(tool_id, user_id=user_id, category=cat.name, before_date=before)
        deleted.update(result.get("deleted", {}))
    return {"toolId": tool_id, "userId": user_id, "deleted": deleted}
