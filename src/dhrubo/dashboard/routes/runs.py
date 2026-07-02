"""`dhrubo.dashboard.routes.runs` — home, host, run-detail, job log streaming.

The "runs" router is the dashboard's largest. It owns:

- ``GET /`` — home (running jobs + recent runs across all hosts).
- ``GET /hosts/{seed_domain}`` — host timeline.
- ``GET /runs/{run_id}`` — run detail (rendered report.md or
  structured JSON).
- ``POST /runs`` — form action that spawns a new audit subprocess.
- ``GET /jobs/{job_id}`` — page that subscribes to the log SSE.
- ``GET /jobs/{job_id}/events`` — SSE endpoint streaming
  ``stdout`` line events + a final ``done`` / ``failed`` /
  ``cancelled`` event.
- ``POST /jobs/{job_id}/cancel`` — terminate the running subprocess.

All routes read state from ``request.app.state``. No module-
level globals — keeps the factory pattern testable.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

from dhrubo.core.run_index import load_run_index
from dhrubo.dashboard.paths import _resolve_template_dir
from dhrubo.dashboard.supervisor import JobState, PoolExhaustedError

router = APIRouter()
_templates = Jinja2Templates(directory=str(_resolve_template_dir()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _output_root(request: Request) -> Path:
    return Path(request.app.state.output_root)


def _supervisor(request: Request) -> Any:
    return request.app.state.supervisor


def _render_markdown(text: str) -> str:
    """Render a Markdown string to HTML.

    Uses the python-markdown package, which is a transitive
    dependency of the ``[pdf]`` extra. We import lazily so
    tests that don't render Markdown (most of them) don't pay
    the import cost.
    """
    try:
        import markdown as _md  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - shouldn't happen
        raise HTTPException(
            status_code=500,
            detail=(
                "python-markdown is required to render reports. "
                "Install with `pip install -e .[pdf]`."
            ),
        ) from exc
    result: Any = _md.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )
    return str(result)


def _resolve_run_dir(run_id: str, output_root: Path) -> Path | None:
    """Find the on-disk run directory for a given ``run_id``.

    Walks every per-host index.json to find a matching row,
    then returns ``output_root / <host_dir>``.
    """
    for row in load_run_index(output_root):
        if row.get("run_id") == run_id:
            host_dir = row.get("run_dir") or run_id
            path = output_root / host_dir
            if path.exists():
                return path
    # Fallback: maybe the directory exists by name even if
    # the index doesn't mention it (e.g. partial run).
    candidate = output_root / run_id
    if candidate.exists():
        return candidate
    return None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Response:
    """Render the dashboard home with running + recent runs."""
    output_root = _output_root(request)
    supervisor = _supervisor(request)
    rows = load_run_index(output_root)
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return _templates.TemplateResponse(
        request,
        "home.html",
        {
            "running_jobs": supervisor.running_jobs(),
            "recent_runs": rows[:25],
            "total_runs": len(rows),
            "output_root": str(output_root),
        },
    )


@router.get("/hosts/{seed_domain:path}", response_class=HTMLResponse)
async def host_view(request: Request, seed_domain: str) -> Response:
    """Render a per-host timeline."""
    output_root = _output_root(request)
    rows = [
        r
        for r in load_run_index(output_root)
        if (r.get("seed_domain") or _derive_seed_domain(r.get("target_url"))) == seed_domain
    ]
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return _templates.TemplateResponse(
        request,
        "host.html",
        {
            "seed_domain": seed_domain,
            "runs": rows,
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    request: Request,
    run_id: str,
    format: str | None = None,
) -> Response:
    """Render a single run's report.md as HTML (or JSON)."""
    output_root = _output_root(request)
    run_dir = _resolve_run_dir(run_id, output_root)
    if run_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"run_id {run_id!r} not found under {output_root}",
        )

    if format == "json":
        data_path = run_dir / "data.json"
        if not data_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"data.json missing in {run_dir}",
            )
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to read data.json: {exc}"
            ) from exc
        return JSONResponse(payload)

    report_path = run_dir / "report.md"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"report.md missing in {run_dir}",
        )
    try:
        md_text = report_path.read_text(encoding="utf-8")
        html_body = _render_markdown(md_text)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read report.md: {exc}"
        ) from exc
    return _templates.TemplateResponse(
        request,
        "report.html",
        {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "report_html": html_body,
            "raw_markdown": md_text,
            "data_path": str(run_dir / "data.json"),
            "diff_path": str(run_dir / "diff.json")
            if (run_dir / "diff.json").exists()
            else None,
        },
    )


# ---------------------------------------------------------------------------
# New run + job lifecycle
# ---------------------------------------------------------------------------


@router.post("/runs", response_model=None)
async def post_runs(
    request: Request,
    url: str | None = Form(default=None),
    pages: str | None = Form(default=None),
    pdf: bool = Form(default=False),
    pdf_format: str = Form(default="a4"),
    concurrency: int = Form(default=4),
    diff_against: str | None = Form(default=None),
    diff_since: str | None = Form(default=None),
    diff_until: str | None = Form(default=None),
    plan_only: bool = Form(default=False),
    dry_run: bool = Form(default=False),
) -> RedirectResponse:
    """Spawn a new audit subprocess and redirect to its job log."""
    if not url and not pages:
        raise HTTPException(
            status_code=400,
            detail="either 'url' or 'pages' must be provided",
        )
    if url and pages:
        raise HTTPException(
            status_code=400,
            detail="only one of 'url' or 'pages' may be set",
        )

    # Build the argv identical to what the user would type
    # in a shell. We deliberately do NOT use shell=True.
    argv: list[str] = [sys.executable, "-m", "dhrubo.commands.cli", "run-audit"]
    if pages:
        argv += ["--pages", pages]
    elif url:
        argv += ["--url", url]
    if plan_only:
        argv.append("--plan-only")
    if dry_run:
        argv.append("--dry-run")
    if not pdf:
        argv.append("--no-pdf")
    argv += ["--pdf-format", pdf_format]
    argv += ["--concurrency", str(concurrency)]
    if diff_against:
        argv += ["--diff-against", diff_against]
    if diff_since:
        argv += ["--diff-since", diff_since]
    if diff_until:
        argv += ["--diff-until", diff_until]

    supervisor = _supervisor(request)
    try:
        job = await supervisor.start(argv)
    except PoolExhaustedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Always 302 to the job log page.
    redirect = RedirectResponse(url=f"/jobs/{job.id}", status_code=302)
    return redirect


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str) -> Response:
    """Render the log-stream page for a single job."""
    supervisor = _supervisor(request)
    job = supervisor.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"job_id {job_id!r} not found"
        )
    return _templates.TemplateResponse(
        request,
        "job.html",
        {
            "job_id": job_id,
            "job": job,
            "argv": job.argv,
        },
    )


@router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str) -> Response:
    """SSE endpoint streaming stdout lines + a terminal event."""
    supervisor = _supervisor(request)
    if supervisor.get(job_id) is None:
        raise HTTPException(
            status_code=404, detail=f"job_id {job_id!r} not found"
        )

    # Local import — sse-starlette is in the [ui] extra; don't
    # hard-require it for everyone who imports the routes.
    from sse_starlette.sse import EventSourceResponse

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        async for ev in supervisor.stream_logs(job_id):
            yield {"event": ev["event"], "data": ev["data"]}

    return EventSourceResponse(event_generator())


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str) -> dict[str, object]:
    """Cancel a running audit subprocess (SIGTERM)."""
    supervisor = _supervisor(request)
    ok = await supervisor.cancel(job_id)
    return {"ok": ok, "job_id": job_id}


# ---------------------------------------------------------------------------
# Helpers for templates (Jinja global)
# ---------------------------------------------------------------------------


def _derive_seed_domain(url: str | None) -> str | None:
    """Best-effort ``seed_domain`` extraction from a URL."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        return host or None
    except Exception:
        return None


_templates.env.filters["state_class"] = lambda s: {
    JobState.running: "running",
    JobState.done: "done",
    JobState.failed: "failed",
    JobState.cancelled: "cancelled",
    JobState.queued: "queued",
}.get(s, "unknown")


__all__ = ["router"]
