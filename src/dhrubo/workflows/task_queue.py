"""Task queue abstraction.

The in-process :class:`InProcessTaskQueue` is the default. It is
intentionally minimal so that a Redis/Arq or RabbitMQ implementation can
drop in without engine changes.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from dhrubo.workflows.task import Task


@runtime_checkable
class TaskQueue(Protocol):
    """Surface a queue must implement to drive the workflow engine."""

    async def put(self, task: Task) -> None: ...
    async def get(self) -> Task: ...
    async def mark_done(self, task: Task) -> None: ...
    def qsize(self) -> int: ...


class InProcessTaskQueue:
    """An :class:`asyncio.Queue`-backed :class:`TaskQueue` for development & tests."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._completed: set[str] = set()

    async def put(self, task: Task) -> None:
        await self._queue.put(task)

    async def get(self) -> Task:
        return await self._queue.get()

    async def mark_done(self, task: Task) -> None:
        self._completed.add(task.task_id)
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def completed(self) -> set[str]:
        return set(self._completed)

    async def join(self) -> None:
        await self._queue.join()
