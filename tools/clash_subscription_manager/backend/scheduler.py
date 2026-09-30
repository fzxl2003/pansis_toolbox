from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.clash_subscription_manager.backend.service import probe_all_nodes, refresh_due_rule_providers, refresh_due_sources


def register_tasks(scheduler: Scheduler) -> None:
    scheduler.add_interval_task(
        tool_id="clash_subscription_manager", name="refresh_due_sources", interval_seconds=60,
        callback=lambda: asyncio.to_thread(refresh_due_sources), run_immediately=False,
    )
    scheduler.add_interval_task(
        tool_id="clash_subscription_manager", name="refresh_due_rule_providers", interval_seconds=60,
        callback=lambda: asyncio.to_thread(refresh_due_rule_providers), run_immediately=False,
    )
    scheduler.add_interval_task(
        tool_id="clash_subscription_manager", name="probe_all_nodes", interval_seconds=120,
        callback=lambda: asyncio.to_thread(probe_all_nodes), run_immediately=False,
    )
