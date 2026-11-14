from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.server_monitor.backend.service import (
    SAMPLE_SECONDS,
    collect_due_servers,
    prune_history,
    upgrade_history_databases,
)


def register_tasks(scheduler: Scheduler) -> None:
    scheduler.add_interval_task(
        tool_id="server_monitor",
        name="upgrade_history_databases",
        interval_seconds=60,
        callback=lambda: asyncio.to_thread(upgrade_history_databases),
        run_immediately=True,
    )
    # Per-user databases are initialized lazily on first API access and on
    # each scheduler tick (collect_due_servers iterates list_user_tool_dbs).
    scheduler.add_interval_task(
        tool_id="server_monitor",
        name="collect_due_servers",
        interval_seconds=SAMPLE_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_servers),
        run_immediately=True,
    )
    scheduler.add_interval_task(
        tool_id="server_monitor",
        name="prune_history",
        interval_seconds=3600,
        callback=lambda: asyncio.to_thread(prune_history),
        run_immediately=True,
    )
