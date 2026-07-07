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


# ============================================================
# Data deletion
# ============================================================

def delete_data(
    tool_id: str,
    user_id: str | None = None,
    category: str | None = None,
    before_date: str | None = None,
) -> dict[str, Any]:
    """Delete data for a tool, optionally filtered by category and/or time.

    Parameters:
        tool_id: The tool whose data should be deleted.
        user_id: For ``user_tool_db`` storage, the user whose database to
            operate on.  If ``None``, the operation applies to **all** users'
            databases for the tool.
        category: If given, only delete tables in this category.  If ``None``,
            delete tables across **all** categories.
        before_date: ISO timestamp.  If given, only delete rows whose
            ``time_column`` value is older than this.  Categories without a
            ``time_column`` are skipped when this is set.

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

    # If before_date is set, skip non-time-based categories
    effective_categories = categories
    if before_date is not None:
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
            deleted.update(_delete_user_tool_data(tool_id, user_id, user_tool_cats, before_date))
        else:
            # All users
            for uid, _path in list_user_tool_dbs(tool_id):
                deleted.update(_delete_user_tool_data(tool_id, uid, user_tool_cats, before_date))

    if platform_cats:
        deleted.update(_delete_platform_data(tool_id, platform_cats, before_date))

    return {"toolId": tool_id, "deleted": deleted}


def _delete_user_tool_data(
    tool_id: str,
    user_id: str,
    categories: list[DataCategory],
    before_date: str | None,
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
                count = _delete_rows(conn, table, cat.time_column, before_date)
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
) -> dict[str, dict[str, int]]:
    """Delete rows from the shared platform database."""
    result: dict[str, dict[str, int]] = {}
    conn = get_connection()
    try:
        for cat in categories:
            for table in cat.tables:
                count = _delete_rows(conn, table, cat.time_column, before_date)
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
) -> int:
    """Delete rows from *table*, optionally filtered by time. Returns count."""
    # Verify the table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return 0

    if time_column is not None and before_date is not None:
        # Verify the time_column exists
        columns = {col["name"] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if time_column not in columns:
            return 0
        cursor = conn.execute(
            f'DELETE FROM "{table}" WHERE "{time_column}" < ?',
            (before_date,),
        )
    elif time_column is None and before_date is None:
        # Delete all rows (truncate)
        cursor = conn.execute(f'DELETE FROM "{table}"')
    else:
        # time_column is None but before_date is set: skip (config data)
        return 0
    return cursor.rowcount or 0


# ============================================================
# Data usage by category
# ============================================================

def get_data_usage_by_category(tool_id: str, user_id: str) -> list[dict[str, Any]]:
    """Return per-category row counts and estimated bytes for a user's tool DB."""
    categories = get_tool_categories(tool_id)
    if not categories:
        return []

    db_path = get_user_tool_db_path(user_id, tool_id)
    if not db_path.exists():
        return []

    result: list[dict[str, Any]] = []
    conn = get_user_tool_connection(user_id, tool_id)
    try:
        for cat in categories:
            total_rows = 0
            table_details: list[dict[str, Any]] = []
            for table in cat.tables:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if row is None:
                    continue
                count_row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()
                count = count_row["c"] if count_row else 0
                total_rows += count
                table_details.append({"table": table, "rows": count})
            result.append({
                "category": cat.name,
                "description": cat.description,
                "timeColumn": cat.time_column,
                "totalRows": total_rows,
                "tables": table_details,
            })
    finally:
        conn.close()

    # Attach file size
    result = _attach_db_size(db_path, result)
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
