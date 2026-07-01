"""Task definition and lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class TaskStatus(StrEnum):
    """Lifecycle states for a workflow task.

    Transitions::

        PENDING  ──► READY ──► RUNNING ──► COMPLETED
                            │           │
                            │           └─► FAILED
                            └────────────────► SKIPPED
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Task:
    """One node in a workflow DAG.

    A task is a unit of work addressed by ``role`` (which agent will run
    it) and ``task_id`` (unique within the workflow). ``depends_on`` is
    a list of ``task_id`` strings that must reach :attr:`TaskStatus.COMPLETED`
    before this task becomes ready.
    """

    task_id: str
    role: str
    depends_on: list[str] = field(default_factory=list)
    input_keys: tuple[str, ...] = ()
    output_keys: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    # Runtime state (not part of the static definition)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    attempts: int = 0
    max_attempts: ClassVar[int] = 3  # overridden per-workflow if needed
