from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.experiment_monitor.backend.service import CHECK_INTERVAL_SECONDS, collect_due_checks


def register_tasks(scheduler: Scheduler) -> None:
    # Per-user databases are initialized lazily on first API access and on
    # each scheduler tick (collect_due_checks iterates list_user_tool_dbs).
    scheduler.add_interval_task(
        tool_id="experiment_monitor",
        name="collect_due_checks",
        interval_seconds=CHECK_INTERVAL_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_checks),
        run_immediately=False,
    )
