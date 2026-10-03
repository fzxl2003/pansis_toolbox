from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.service_navigator.backend.service import collect_due_health_checks


def register_tasks(scheduler: Scheduler) -> None:
    """Check due service-navigator HTTP endpoints once per minute."""
    scheduler.add_interval_task(
        tool_id="service_navigator",
        name="collect_due_health_checks",
        interval_seconds=60,
        callback=lambda: asyncio.to_thread(collect_due_health_checks),
        run_immediately=False,
    )
