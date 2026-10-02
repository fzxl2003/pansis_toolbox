from __future__ import annotations

import asyncio

from backend.app.services.scheduler_service import Scheduler
from tools.airplay_subscription_manager.backend.service import probe_due_nodes, refresh_due_profiles, refresh_due_rule_providers, refresh_due_sources


def register_tasks(scheduler: Scheduler) -> None:
    scheduler.add_interval_task(
        tool_id="airplay_subscription_manager", name="refresh_due_sources", interval_seconds=60,
        callback=lambda: asyncio.to_thread(refresh_due_sources), run_immediately=False,
    )
    scheduler.add_interval_task(
        tool_id="airplay_subscription_manager", name="refresh_due_rule_providers", interval_seconds=60,
        callback=lambda: asyncio.to_thread(refresh_due_rule_providers), run_immediately=False,
    )
    scheduler.add_interval_task(
        tool_id="airplay_subscription_manager", name="refresh_due_profiles", interval_seconds=60,
        callback=lambda: asyncio.to_thread(refresh_due_profiles), run_immediately=False,
    )
    scheduler.add_interval_task(
        tool_id="airplay_subscription_manager", name="probe_due_nodes", interval_seconds=60,
        callback=lambda: asyncio.to_thread(probe_due_nodes), run_immediately=False,
    )
