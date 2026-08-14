from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.tensorboard_progress_monitor.backend.service import collect_due_reports


def register_tasks(scheduler: Scheduler) -> None:
    # Individual task intervals are evaluated inside collect_due_reports.
    scheduler.add_interval_task(
        tool_id="tensorboard_progress_monitor",
        name="collect_due_reports",
        interval_seconds=30,
        callback=lambda: asyncio.to_thread(collect_due_reports),
        run_immediately=False,
    )
