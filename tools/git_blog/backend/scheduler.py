from __future__ import annotations
import asyncio
from backend.app.services.scheduler_service import Scheduler
from tools.git_blog.backend.service import sync_due_blogs

def register_tasks(scheduler: Scheduler) -> None:
    scheduler.add_interval_task(tool_id="git_blog", name="sync_due_blogs", interval_seconds=15, callback=lambda: asyncio.to_thread(sync_due_blogs), run_immediately=True)
