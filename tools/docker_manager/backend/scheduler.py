from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.docker_manager.backend.auto_updates import process_updates


def register_tasks(scheduler: Scheduler) -> None:
    scheduler.add_interval_task(
        tool_id="docker_manager",
        name="auto_updates",
        interval_seconds=15,
        callback=lambda: asyncio.to_thread(process_updates),
        run_immediately=True,
    )
