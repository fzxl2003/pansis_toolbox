from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.ssh_workspace.backend.service import SCHEDULER_INTERVAL_SECONDS, collect_due_tasks, init_database


def register_tasks(scheduler: Scheduler) -> None:
    init_database()
    scheduler.add_interval_task(
        tool_id="ssh_workspace",
        name="collect_due_tasks",
        interval_seconds=SCHEDULER_INTERVAL_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_tasks),
        run_immediately=False,
    )
