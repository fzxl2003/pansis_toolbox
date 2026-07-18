from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.ssh_workspace.backend.service import SCHEDULER_INTERVAL_SECONDS, collect_due_tasks


def register_tasks(scheduler: Scheduler) -> None:
    # Per-user databases are initialized lazily on first API access and on
    # each scheduler tick (collect_due_tasks iterates list_user_tool_dbs).
    scheduler.add_interval_task(
        tool_id="ssh_workspace",
        name="collect_due_tasks",
        interval_seconds=SCHEDULER_INTERVAL_SECONDS,
        callback=lambda: asyncio.to_thread(collect_due_tasks),
        run_immediately=False,
    )
