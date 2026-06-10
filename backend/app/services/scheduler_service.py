from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ScheduledCallable = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class ScheduledTask:
    tool_id: str
    name: str
    interval_seconds: int
    callback: ScheduledCallable
    run_immediately: bool = False


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []
        self._running_tasks: list[asyncio.Task] = []
        self._started = False

    def add_interval_task(
        self,
        *,
        tool_id: str,
        name: str,
        interval_seconds: int,
        callback: ScheduledCallable,
        run_immediately: bool = False,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")
        self._tasks = [task for task in self._tasks if not (task.tool_id == tool_id and task.name == name)]
        self._tasks.append(
            ScheduledTask(
                tool_id=tool_id,
                name=name,
                interval_seconds=interval_seconds,
                callback=callback,
                run_immediately=run_immediately,
            )
        )

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._running_tasks = [asyncio.create_task(self._run_loop(task)) for task in self._tasks]
        logger.info("Started %s scheduled task(s)", len(self._running_tasks))

    async def stop(self) -> None:
        if not self._started:
            return
        for task in self._running_tasks:
            task.cancel()
        await asyncio.gather(*self._running_tasks, return_exceptions=True)
        self._running_tasks = []
        self._started = False

    async def _run_loop(self, task: ScheduledTask) -> None:
        if task.run_immediately:
            await self._run_once(task)
        while True:
            await asyncio.sleep(task.interval_seconds)
            await self._run_once(task)

    async def _run_once(self, task: ScheduledTask) -> None:
        try:
            result = task.callback()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - scheduled tool failures must not break the host app.
            logger.exception("Scheduled task failed: tool=%s task=%s", task.tool_id, task.name)


scheduler = Scheduler()
