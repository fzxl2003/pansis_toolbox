from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.experiment_monitor.backend.service import CHECK_INTERVAL_SECONDS, collect_due_checks, init_database


def register_tasks(scheduler: Scheduler) -> None:
    init_database()
    scheduler.add_interval_task(
        tool_id="experiment_monitor",
        name="collect_due_checks",
        interval_seconds=CHECK_INTERVAL_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_checks),
        run_immediately=False,
    )
