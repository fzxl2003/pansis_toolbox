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

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.app.core.config import get_settings
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
    """

    name: str
    tables: list[str]
    time_column: str | None
    description: str
    storage: str = "user_tool_db"  # "user_tool_db" | "platform_db"


# Global registry: tool_id -> list of categories
_TOOL_CATEGORIES: dict[str, list[DataCategory]] = {}


def register_tool_categories(tool_id: str, categories: list[DataCategory]) -> None:
    """Register data categories for a tool.  Called at tool import time."""
    _TOOL_CATEGORIES[tool_id] = list(categories)


def get_tool_categories(tool_id: str) -> list[DataCategory]:
    """Return the registered categories for *tool_id* (empty if none)."""
    return list(_TOOL_CATEGORIES.get(tool_id, []))


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
    categories = get_tool_categories(tool_id)
    if not categories:
        return {"toolId": tool_id, "deleted": {}, "message": "未注册数据分类"}

    # Filter categories
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

    deleted: dict[str, dict[str, int]] = {}

    # Determine which storage(s) to operate on
    user_tool_cats = [c for c in effective_categories if c.storage == "user_tool_db"]
    platform_cats = [c for c in effective_categories if c.storage == "platform_db"]

    if user_tool_cats:
        if user_id is not None:
            deleted.update(_delete_user_tool_data(tool_id, user_id, user_tool_cats, before_date, after_date))
        else:
            # All users
            for uid, _path in list_user_tool_dbs(tool_id):
                deleted.update(_delete_user_tool_data(tool_id, uid, user_tool_cats, before_date, after_date))

    if platform_cats:
        deleted.update(_delete_platform_data(tool_id, platform_cats, before_date, after_date))

    return {"toolId": tool_id, "deleted": deleted}


def _delete_user_tool_data(
    tool_id: str,
    user_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
) -> dict[str, dict[str, int]]:
    """Delete rows from a single user's per-tool database."""
    result: dict[str, dict[str, int]] = {}
    try:
        conn = get_user_tool_connection(user_id, tool_id)
    except Exception:
        return result
    try:
        for cat in categories:
            for table in cat.tables:
                count = _delete_rows(conn, table, cat.time_column, before_date, after_date)
                if count > 0:
                    result.setdefault(cat.name, {})[table] = count
        conn.commit()
    finally:
        conn.close()
    return result


def _delete_platform_data(
    tool_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
) -> dict[str, dict[str, int]]:
    """Delete rows from the shared platform database."""
    result: dict[str, dict[str, int]] = {}
    conn = get_connection()
    try:
        for cat in categories:
            for table in cat.tables:
                count = _delete_rows(conn, table, cat.time_column, before_date, after_date)
                if count > 0:
                    result.setdefault(cat.name, {})[table] = count
        conn.commit()
    finally:
        conn.close()
    return result


def _delete_rows(
    conn: sqlite3.Connection,
    table: str,
    time_column: str | None,
    before_date: str | None,
    after_date: str | None = None,
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
    if time_column is not None and has_time_filter:
        # Verify the time_column exists
        columns = {col["name"] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()}
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
        cursor = conn.execute(
            f'DELETE FROM "{table}" WHERE {" AND ".join(clauses)}',
            params,
        )
    elif time_column is None and not has_time_filter:
        # Delete all rows (truncate)
        cursor = conn.execute(f'DELETE FROM "{table}"')
    else:
        # time_column is None but a time filter is set: skip (config data)
        return 0
    return cursor.rowcount or 0


def _count_rows(
    conn: sqlite3.Connection,
    table: str,
    time_column: str | None,
    before_date: str | None,
    after_date: str | None = None,
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
    if time_column is not None and has_time_filter:
        columns = {col["name"] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()}
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
        count_row = conn.execute(
            f'SELECT COUNT(*) AS c FROM "{table}" WHERE {" AND ".join(clauses)}',
            params,
        ).fetchone()
    elif time_column is None and not has_time_filter:
        count_row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()
    else:
        # time_column is None but a time filter is set: skip (config data)
        return 0
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
    categories = get_tool_categories(tool_id)
    if not categories:
        return 0

    if category is not None:
        categories = [c for c in categories if c.name == category]
        if not categories:
            return 0

    has_time_filter = before_date is not None or after_date is not None
    if has_time_filter:
        categories = [c for c in categories if c.time_column is not None]
        if not categories:
            return 0

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
        total += _count_platform_data(tool_id, platform_cats, before_date, after_date)

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
    try:
        conn = get_user_tool_connection(user_id, tool_id)
    except Exception:
        return 0
    try:
        for cat in categories:
            for table in cat.tables:
                total += _count_rows(conn, table, cat.time_column, before_date, after_date)
    finally:
        conn.close()
    return total


def _count_platform_data(
    tool_id: str,
    categories: list[DataCategory],
    before_date: str | None,
    after_date: str | None = None,
) -> int:
    """Count matching rows from the shared platform database."""
    total = 0
    conn = get_connection()
    try:
        for cat in categories:
            for table in cat.tables:
                total += _count_rows(conn, table, cat.time_column, before_date, after_date)
    finally:
        conn.close()
    return total


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

    # Determine the list of (user_id, db_path) pairs to query.
    if user_id is not None:
        db_path = get_user_tool_db_path(user_id, tool_id)
        if not db_path.exists():
            return []
        user_dbs: list[tuple[str, Path]] = [(user_id, db_path)]
    else:
        user_dbs = list_user_tool_dbs(tool_id)
        if not user_dbs:
            return []

    # Aggregate row counts across all user DBs.
    cat_totals: dict[str, int] = {c.name: 0 for c in categories}
    cat_tables: dict[str, dict[str, int]] = {c.name: {} for c in categories}
    total_db_size = 0

    for uid, db_path in user_dbs:
        try:
            total_db_size += db_path.stat().st_size
        except OSError:
            pass
        try:
            conn = get_user_tool_connection(uid, tool_id)
        except Exception:
            continue
        try:
            for cat in categories:
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
            "tables": table_details,
        })

    # Attach aggregated file size
    for cat in result:
        cat["dbBytes"] = total_db_size
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
