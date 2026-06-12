from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.server_monitor.backend.service import SAMPLE_SECONDS, collect_due_servers, init_monitor_database


def register_tasks(scheduler: Scheduler) -> None:
    init_monitor_database()
    scheduler.add_interval_task(
        tool_id="server_monitor",
        name="collect_due_servers",
        interval_seconds=SAMPLE_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_servers),
        run_immediately=True,
    )
