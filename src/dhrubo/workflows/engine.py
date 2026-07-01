"""Workflow engine: async DAG executor.

The engine is intentionally simple:

1. Build a :class:`Workflow` from a list of :class:`Task` nodes.
2. Repeatedly pick the next *wave* of tasks whose dependencies are all
   :attr:`TaskStatus.COMPLETED` (or skipped).
3. Schedule each task in the wave concurrently.
4. Repeat until all tasks are in a terminal state.

Concurrency is bounded by ``max_concurrency``; failures do not abort the
run unless ``fail_fast=True`` — they are recorded in the result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent, agent_registry
from dhrubo.core.errors import WorkflowError
from dhrubo.core.logger import get_logger
from dhrubo.core.telemetry import span
from dhrubo.memory.session_memory import SessionMemory
from dhrubo.workflows.task import Task, TaskStatus
from dhrubo.workflows.task_queue import InProcessTaskQueue, TaskQueue

_log = get_logger("workflows")


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # ran to completion but with at least one failed task


@dataclass(slots=True)
class Workflow:
    """A declarative DAG of :class:`Task` nodes."""

    name: str
    tasks: list[Task] = field(default_factory=list)

    def task(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        raise WorkflowError(
            f"Unknown task_id '{task_id}'",
            context={"workflow": self.name, "task_id": task_id},
        )

    def add(self, task: Task) -> None:
        # Validate references to existing tasks.
        for dep in task.depends_on:
            if dep == task.task_id:
                raise WorkflowError(
                    f"Task '{task.task_id}' cannot depend on itself",
                    context={"workflow": self.name},
                )
            if not any(t.task_id == dep for t in self.tasks):
                raise WorkflowError(
                    f"Task '{task.task_id}' depends on unknown task '{dep}'",
                    context={"workflow": self.name, "dep": dep},
                )
        self.tasks.append(task)

    def roots(self) -> list[Task]:
        return [t for t in self.tasks if not t.depends_on]

    def validate(self) -> None:
        """Check that the DAG has no cycles and all deps are known."""
        if not self.tasks:
            raise WorkflowError(
                f"Workflow '{self.name}' has no tasks",
                context={"workflow": self.name},
            )
        # Cycle detection via DFS.
        white, gray, black = 0, 1, 2
        color: dict[str, int] = {t.task_id: white for t in self.tasks}

        def visit(task_id: str) -> None:
            c = color[task_id]
            if c == gray:
                raise WorkflowError(
                    f"Cycle detected in workflow '{self.name}'",
                    context={"workflow": self.name, "task": task_id},
                )
            if c == black:
                return
            color[task_id] = gray
            for dep in self.task(task_id).depends_on:
                visit(dep)
            color[task_id] = black

        for t in self.tasks:
            visit(t.task_id)


@dataclass(slots=True)
class WorkflowResult:
    """The outcome of a workflow run."""

    workflow: str
    status: WorkflowStatus
    task_results: dict[str, AgentResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowEngine:
    """Executes a :class:`Workflow` using an injected :class:`TaskQueue`."""

    def __init__(
        self,
        queue: TaskQueue | None = None,
        *,
        max_concurrency: int = 4,
        fail_fast: bool = False,
    ) -> None:
        self._queue: TaskQueue = queue or InProcessTaskQueue()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._fail_fast = fail_fast
        self._registry = agent_registry

    @property
    def queue(self) -> TaskQueue:
        return self._queue

    def register_agent(self, agent_cls: type[BaseAgent]) -> None:
        """Explicitly register a custom agent subclass with the engine."""
        self._registry.register(agent_cls)

    async def run(
        self,
        workflow: Workflow,
        *,
        memory: SessionMemory | None = None,
        llm: Any = None,
        tracer: Any = None,
        initial_inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute the workflow to completion.

        Args:
            workflow: The DAG to execute.
            memory: Session memory to share between tasks. A new one is
                created if not provided.
            llm: LLM provider to inject into agent contexts.
            tracer: Tracer to inject into agent contexts.
            initial_inputs: Keys to seed session memory with.
        """
        workflow.validate()
        mem = memory or SessionMemory(namespace=workflow.name)
        if initial_inputs:
            for k, v in initial_inputs.items():
                await mem.write(k, v)

        result = WorkflowResult(workflow=workflow.name, status=WorkflowStatus.RUNNING)
        if metadata:
            result.metadata.update(metadata)
        # Local status map so we can advance the wave.
        status = {t.task_id: t.status for t in workflow.tasks}

        try:
            with span(tracer, "workflow.run", workflow=workflow.name) as wf_span:
                while True:
                    ready: list[Task] = []
                    for t in workflow.tasks:
                        if status[t.task_id] not in (
                            TaskStatus.PENDING,
                            TaskStatus.READY,
                        ):
                            continue
                        if all(
                            status.get(dep) == TaskStatus.COMPLETED
                            for dep in t.depends_on
                        ):
                            status[t.task_id] = TaskStatus.READY
                            ready.append(t)

                    if not ready:
                        break

                    coros = [self._execute_task(t, mem, llm, tracer, result) for t in ready]
                    outcomes = await asyncio.gather(*coros, return_exceptions=True)
                    for t, outcome in zip(ready, outcomes, strict=True):
                        if isinstance(outcome, BaseException):
                            status[t.task_id] = TaskStatus.FAILED
                            result.errors[t.task_id] = repr(outcome)
                            t.status = TaskStatus.FAILED
                            t.error = repr(outcome)
                        else:
                            status[t.task_id] = outcome
                            t.status = outcome
                        if (
                            self._fail_fast
                            and status[t.task_id] == TaskStatus.FAILED
                        ):
                            raise WorkflowError(
                                "fail_fast triggered",
                                context={"task": t.task_id, "error": t.error},
                            )
                    wf_span.set_attribute("wave_size", len(ready))

            # Decide terminal status.
            if any(v == TaskStatus.FAILED for v in status.values()):
                result.status = (
                    WorkflowStatus.FAILED if self._fail_fast else WorkflowStatus.PARTIAL
                )
            else:
                result.status = WorkflowStatus.COMPLETED
        except Exception:
            result.status = WorkflowStatus.FAILED
            raise
        return result

    async def _execute_task(
        self,
        task: Task,
        memory: SessionMemory,
        llm: Any,
        tracer: Any,
        result: WorkflowResult,
    ) -> TaskStatus:
        async with self._sem:
            with span(tracer, "task.run", task=task.task_id, role=task.role) as sp:
                task.status = TaskStatus.RUNNING
                task.attempts += 1
                inputs: dict[str, Any] = {}
                for key in task.input_keys:
                    inputs[key] = await memory.read(key)
                inputs.update(task.metadata.get("inputs", {}))

                ctx = AgentContext(
                    role=task.role,
                    inputs=inputs,
                    session_memory=memory,
                    llm=llm,
                    tracer=tracer,
                    metadata={"task_id": task.task_id, "attempts": task.attempts},
                )

                agent = self._registry.instantiate(task.role)
                ar = await agent.safe_execute(ctx)
                result.task_results[task.task_id] = ar
                sp.set_attribute("success", ar.success)
                if not ar.success:
                    result.errors[task.task_id] = ar.error or "unknown"
                    task.error = ar.error
                    return TaskStatus.FAILED

                # Persist outputs.
                for key, value in ar.outputs.items():
                    await memory.write(key, value)
                return TaskStatus.COMPLETED


# Sentinel import to keep mypy happy when callers import the symbol name only.
def _unused_marker(_: Iterable[Task]) -> None:
    return None
