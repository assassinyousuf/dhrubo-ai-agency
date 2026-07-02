"""`dhrubo.dashboard.supervisor` — async process pool for audit subprocesses.

The dashboard's "New run" button triggers a fresh ``dhrubo
run-audit`` invocation. We need four behaviours:

1. **Async, non-blocking.** The FastAPI event loop must stay
   responsive even while the subprocess streams stdout.
2. **Streamable.** Per-job stdout / stderr must be consumable
   line-by-line by an SSE handler.
3. **Capped.** Never more than ``max_concurrent`` jobs alive;
   additional starts are rejected.
4. **Cancellable.** User clicks "Cancel" → SIGTERM → mark
   ``cancelled`` → drain remaining buffered output.

The supervisor is intentionally minimal. It's not a generic
job queue — it's a thin wrapper around :func:`asyncio.create_subprocess_exec`
that knows about a single ``output_root`` + how to invoke the
CLI. State (the ``Job`` objects) is kept in-memory; if the
dashboard restarts, jobs are orphaned (their log lines are
gone). That's acceptable for an MVP local dashboard.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobState(StrEnum):
    """Lifecycle of a :class:`Job`."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


@dataclass(slots=True)
class Job:
    """A single :class:`RunSupervisor` subprocess invocation."""

    id: str
    argv: list[str]
    state: JobState = JobState.queued
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    exit_code: int | None = None
    pid: int | None = None
    # Output buffered in memory until SSE consumers drain it.
    # Sized to bound memory for very chatty runs.
    lines: list[str] = field(default_factory=list)
    # How many SSE consumers are currently attached. Used by
    # the cleanup decision when a job ends.
    consumer_count: int = 0
    # Populated when the producer task has finished writing —
    # lets SSE handlers know there will be no further lines.
    _stream_complete: asyncio.Event | None = None
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)


class PoolExhaustedError(RuntimeError):
    """Raised when :meth:`RunSupervisor.start` would exceed the cap."""


class RunSupervisor:
    """Asyncio-based process pool for ``dhrubo run-audit`` subprocesses.

    One instance per FastAPI app (singleton via ``app.state``).
    """

    _MAX_BUFFERED_LINES = 5000

    def __init__(
        self,
        *,
        max_concurrent: int,
        cwd: Path | None = None,
    ) -> None:
        self._max_concurrent = max(1, max_concurrent)
        self._cwd = cwd
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Synchronous inspection (used by route handlers)
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return sorted(
            self._jobs.values(),
            key=lambda j: j.started_at,
            reverse=True,
        )

    def running_jobs(self) -> list[Job]:
        return [j for j in self.list_jobs() if j.state == JobState.running]

    def finished_jobs(self) -> list[Job]:
        return [
            j
            for j in self.list_jobs()
            if j.state in (JobState.done, JobState.failed, JobState.cancelled)
        ]

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def start(self, argv: list[str]) -> Job:
        """Spawn a new subprocess.

        Args:
            argv: Full argv (e.g. ``["python", "-m", "dhrubo.commands.cli",
                "run-audit", "--url", "https://example.com/", "--no-pdf"]``).
                ``shell=True`` is never used; each token is passed
                verbatim.

        Returns:
            The :class:`Job` immediately so the caller can
            redirect to ``/jobs/{id}``.

        Raises:
            PoolExhaustedError: if N concurrent jobs are already running.
        """
        async with self._lock:
            running = [j for j in self._jobs.values() if j.state == JobState.running]
            if len(running) >= self._max_concurrent:
                raise PoolExhaustedError(
                    f"max_concurrent={self._max_concurrent}; "
                    f"{len(running)} runs already in flight"
                )
            job_id = uuid.uuid4().hex[:12]
            job = Job(id=job_id, argv=list(argv), _stream_complete=asyncio.Event())
            self._jobs[job_id] = job

        # Spawn outside the lock — IO-bound.
        env = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout stream
            cwd=str(self._cwd) if self._cwd else None,
            env=env,
        )
        job._proc = proc
        job.pid = proc.pid
        job.state = JobState.running

        # Producer task: read stdout line-by-line, buffer,
        # mark stream complete on EOF.
        task = asyncio.create_task(
            self._consume_stdout(job),
            name=f"supervisor-{job_id}",
        )
        # Also: when the process exits, decide done / failed / cancelled.
        self._exit_task = asyncio.create_task(
            self._await_exit(job, task),
            name=f"supervisor-exit-{job_id}",
        )
        return job

    async def cancel(self, job_id: str) -> bool:
        """Send SIGTERM to the job's subprocess. Idempotent."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        proc = job._proc
        if proc is None or proc.returncode is not None:
            return False
        try:
            proc.terminate()
        except ProcessLookupError:
            return False
        return True

    async def stream_logs(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Async generator that yields SSE event dicts for a job.

        Each yielded dict has keys ``event`` (``"line"``,
        ``"done"``, ``"failed"``, ``"cancelled"``) and ``data``
        (the line text or final payload). The consumer is
        responsible for translating these into SSE wire
        format (see :mod:`dhrubo.dashboard.routes.runs`).
        """
        job = self._jobs.get(job_id)
        if job is None:
            yield {"event": "failed", "data": f"unknown job_id {job_id!r}"}
            return

        job.consumer_count += 1
        try:
            # First, replay any buffered lines (so a late SSE
            # consumer doesn't miss the early output).
            buffer_idx = 0
            while buffer_idx < len(job.lines):
                yield {"event": "line", "data": job.lines[buffer_idx]}
                buffer_idx += 1

            # Then wait for new lines / final event.
            complete = job._stream_complete
            if complete is None:  # pragma: no cover - defensive
                return
            # Wait for stream completion. We poll the buffer
            # length instead of using a Condition because
            # producing lines shouldn't have to acquire a lock
            # on the hot path.
            last_seen = buffer_idx
            while not complete.is_set():
                if len(job.lines) > last_seen:
                    while last_seen < len(job.lines):
                        yield {"event": "line", "data": job.lines[last_seen]}
                        last_seen += 1
                # Avoid a busy-loop on idle jobs.
                await asyncio.sleep(0.1)
            # Drain any final buffered lines.
            while last_seen < len(job.lines):
                yield {"event": "line", "data": job.lines[last_seen]}
                last_seen += 1
            # Terminal event.
            if job.state == JobState.cancelled:
                yield {
                    "event": "cancelled",
                    "data": f"cancelled (exit={job.exit_code})",
                }
            elif job.state == JobState.failed:
                yield {
                    "event": "failed",
                    "data": f"failed (exit={job.exit_code})",
                }
            else:
                yield {
                    "event": "done",
                    "data": f"done (exit={job.exit_code})",
                }
        finally:
            job.consumer_count = max(0, job.consumer_count - 1)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _consume_stdout(self, job: Job) -> None:
        """Read stdout line-by-line and append to ``job.lines``."""
        proc = job._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - defensive
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                # Decode, strip trailing newline (browser will
                # get its own rendering).
                try:
                    text = line.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:  # pragma: no cover
                    text = repr(line)
                # Bound the buffer.
                if len(job.lines) >= self._MAX_BUFFERED_LINES:
                    job.lines.pop(0)
                job.lines.append(text)
        finally:
            # Producer is done — unblock stream consumers.
            complete = job._stream_complete
            if complete is not None:
                complete.set()

    async def _await_exit(self, job: Job, producer_task: asyncio.Task[None]) -> None:
        """Wait for the process to exit; classify done/failed/cancelled."""
        proc = job._proc
        if proc is None:  # pragma: no cover - defensive
            return
        try:
            rc = await proc.wait()
        finally:
            # Ensure the producer finishes reading stdout before
            # we close the door on the SSE generator.
            try:
                await asyncio.wait_for(producer_task, timeout=2.0)
            except TimeoutError:  # pragma: no cover - very chatty subprocess
                producer_task.cancel()
        job.exit_code = rc
        job.ended_at = datetime.now(UTC)
        if job.state == JobState.cancelled:
            return  # already classified
        if rc == 0:
            job.state = JobState.done
        elif rc < 0:
            # Negative: killed by signal (e.g. SIGTERM -> -15).
            # Treat as cancelled (most likely the user clicked cancel).
            job.state = JobState.cancelled
        else:
            job.state = JobState.failed


__all__ = ["Job", "JobState", "PoolExhaustedError", "RunSupervisor"]
