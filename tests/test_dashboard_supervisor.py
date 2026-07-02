"""`tests.test_dashboard_supervisor` — RunSupervisor unit tests.

Drives the supervisor with tiny inline ``python -c`` subprocesses
so tests stay under 5s and hermetic — no real audit pipeline, no
network, no fixtures on disk.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from dhrubo.dashboard.supervisor import (
    Job,
    JobState,
    PoolExhaustedError,
    RunSupervisor,
)

# ---------------------------------------------------------------------------
# Small helpers — used by every async test below.
# ---------------------------------------------------------------------------


def _py_argv(script: str) -> list[str]:
    return [sys.executable, "-c", script]


async def _drain_supervisor(supervisor: RunSupervisor, job_id: str) -> list[dict[str, str]]:
    """Collect every SSE event the supervisor emits for ``job_id``
    until the stream completes."""
    events: list[dict[str, str]] = []
    async for ev in supervisor.stream_logs(job_id):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Lifecycle: clean exit
# ---------------------------------------------------------------------------


async def test_supervisor_starts_subprocess_captures_stdout(tmp_path: Path) -> None:
    """A quick-printing subprocess is captured + replayed via SSE."""
    sup = RunSupervisor(max_concurrent=2, cwd=tmp_path)
    argv = _py_argv("print('hello world')")
    job = await sup.start(argv)
    assert isinstance(job, Job)
    assert job.state == JobState.running
    assert job.pid is not None

    events = await _drain_supervisor(sup, job.id)
    # At least one stdout event with our payload + a final "done" event.
    stdout_payloads = [e["data"] for e in events if e["event"] == "stdout"]
    assert any("hello world" in p for p in stdout_payloads), events
    final = next((e for e in events if e["event"] in {"done", "failed"}), None)
    assert final is not None
    assert final["event"] == "done"


async def test_supervisor_emits_done_event_on_clean_exit(tmp_path: Path) -> None:
    sup = RunSupervisor(max_concurrent=1, cwd=tmp_path)
    argv = _py_argv("import sys; sys.exit(0)")
    job = await sup.start(argv)
    events = await _drain_supervisor(sup, job.id)
    events_by_type = [e["event"] for e in events]
    assert "done" in events_by_type
    assert sup.get(job.id).state == JobState.done
    assert sup.get(job.id).exit_code == 0


async def test_supervisor_emits_failed_event_on_nonzero_exit(tmp_path: Path) -> None:
    sup = RunSupervisor(max_concurrent=1, cwd=tmp_path)
    argv = _py_argv("import sys; sys.exit(7)")
    job = await sup.start(argv)
    events = await _drain_supervisor(sup, job.id)
    events_by_type = [e["event"] for e in events]
    assert "failed" in events_by_type
    assert sup.get(job.id).state == JobState.failed
    assert sup.get(job.id).exit_code == 7


async def test_supervisor_cancel_terminates_subprocess(tmp_path: Path) -> None:
    sup = RunSupervisor(max_concurrent=1, cwd=tmp_path)
    # Long-running sleeper we can interrupt.
    argv = _py_argv(
        "import sys, time\n"
        "for _ in range(20):\n"
        "    print('tick')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.05)\n"
    )
    job = await sup.start(argv)
    # Give the process time to spin up.
    await asyncio.sleep(0.05)
    cancelled = await sup.cancel(job.id)
    assert cancelled is True
    # Wait for the exit task to classify the job.
    await _wait_for_terminal(sup, job.id, timeout=3.0)
    assert sup.get(job.id).state == JobState.cancelled


# ---------------------------------------------------------------------------
# Pool cap
# ---------------------------------------------------------------------------


async def test_supervisor_pool_cap_rejects_extra_starts(tmp_path: Path) -> None:
    """Two concurrent jobs are OK; the third start raises PoolExhaustedError."""
    sup = RunSupervisor(max_concurrent=2, cwd=tmp_path)
    slow = _py_argv(
        "import sys, time\n"
        "time.sleep(0.5)\n"
    )
    j1 = await sup.start(slow)
    j2 = await sup.start(slow)
    assert {j1.state, j2.state} == {JobState.running}
    with pytest.raises(PoolExhaustedError):
        await sup.start(slow)
    # Cancel the two we did start so the test can exit cleanly.
    await sup.cancel(j1.id)
    await sup.cancel(j2.id)
    await _wait_for_terminal(sup, j1.id, timeout=3.0)
    await _wait_for_terminal(sup, j2.id, timeout=3.0)


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------


async def test_supervisor_keeps_recent_jobs_in_memory(tmp_path: Path) -> None:
    """Finished jobs are kept; running_jobs only returns live ones."""
    sup = RunSupervisor(max_concurrent=2, cwd=tmp_path)
    argv = _py_argv("print('x')")
    job = await sup.start(argv)
    await _drain_supervisor(sup, job.id)

    listing = sup.list_jobs()
    assert any(j.id == job.id for j in listing)
    assert all(j.state in {JobState.done, JobState.failed} for j in listing)
    assert sup.running_jobs() == []


async def test_supervisor_separates_jobs_by_id(tmp_path: Path) -> None:
    sup = RunSupervisor(max_concurrent=4, cwd=tmp_path)
    j1 = await sup.start(_py_argv("print('one')"))
    j2 = await sup.start(_py_argv("print('two')"))
    assert j1.id != j2.id
    assert sup.get(j1.id) is j1
    assert sup.get(j2.id) is j2
    # Drain both.
    await asyncio.gather(
        _drain_supervisor(sup, j1.id),
        _drain_supervisor(sup, j2.id),
    )


# ---------------------------------------------------------------------------
# Late consumer
# ---------------------------------------------------------------------------


async def test_supervisor_replays_buffered_lines_to_late_consumer(tmp_path: Path) -> None:
    """A subscriber that connects after the producer has buffered
    N lines still receives all N (within the buffer cap)."""
    sup = RunSupervisor(max_concurrent=1, cwd=tmp_path)
    argv = _py_argv(
        "import sys\n"
        "for i in range(5):\n"
        "    print(f'line-{i}')\n"
        "    sys.stdout.flush()\n"
    )
    job = await sup.start(argv)
    # Wait for the producer to finish writing.
    await _wait_for_terminal(sup, job.id)
    # Late subscriber — should see the buffered lines + a "done".
    events = await _drain_supervisor(sup, job.id)
    stdout = [e["data"] for e in events if e["event"] == "stdout"]
    assert len(stdout) >= 5
    assert any("line-0" in s for s in stdout)
    assert any("line-4" in s for s in stdout)


async def _wait_for_terminal(sup: RunSupervisor, job_id: str, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = sup.get(job_id)
        if job is not None and job.state in {JobState.done, JobState.failed, JobState.cancelled}:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not reach terminal state within timeout")
