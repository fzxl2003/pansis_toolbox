from __future__ import annotations

import asyncio
import logging

import pytest

from backend.app.services.scheduler_service import Scheduler


def test_scheduler_keeps_running_after_task_error(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"bad": 0, "good": 0}

    def bad_task() -> None:
        calls["bad"] += 1
        raise RuntimeError("boom")

    def good_task() -> None:
        calls["good"] += 1

    async def run_scheduler() -> None:
        scheduler = Scheduler()
        scheduler.add_interval_task(tool_id="bad_tool", name="bad", interval_seconds=1, callback=bad_task, run_immediately=True)
        scheduler.add_interval_task(tool_id="good_tool", name="good", interval_seconds=1, callback=good_task, run_immediately=True)
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

    with caplog.at_level(logging.ERROR):
        asyncio.run(run_scheduler())

    assert calls["bad"] >= 1
    assert calls["good"] >= 1
    assert "Scheduled task failed: tool=bad_tool task=bad" in caplog.text
