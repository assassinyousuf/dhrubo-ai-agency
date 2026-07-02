"""`dhrubo.dashboard.routes.diff` — diff page + JSON endpoint.

Renders a simple form (``--url``, ``--since``, ``--until``)
and an XHR-driven result panel. The page itself is a thin
wrapper around the standalone ``dhrubo diff`` logic; we
re-use :func:`dhrubo.tools.diff_tool.compute_diff` and
:func:`dhrubo.core.run_window.select_runs_in_window` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from dhrubo.core.run_index import load_sub_reports_for_run
from dhrubo.core.run_window import select_runs_in_window
from dhrubo.core.timeparse import parse_window
from dhrubo.dashboard.paths import _resolve_template_dir
from dhrubo.tools.diff_tool import compute_diff

router = APIRouter()
_templates = Jinja2Templates(directory=str(_resolve_template_dir()))


@router.get("/diff", response_class=HTMLResponse)
async def diff_form(request: Request) -> Response:
    """Render the diff form."""
    return _templates.TemplateResponse(
        request,
        "diff.html",
        {"result": None, "form": {}},
    )


@router.post("/api/diff")
async def diff_compute(
    request: Request,
    url: str = Form(default=""),
    since: str | None = Form(default=None),
    until: str | None = Form(default=None),
    as_json: bool = Form(default=False),
) -> Response:
    """Compute earliest-vs-latest diff inside a time window.

    Mirrors the standalone ``dhrubo diff`` subcommand. Returns
    a JSON payload suitable for the browser-side JS to render.
    """
    output_root = Path(request.app.state.output_root)
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required")

    try:
        window = parse_window(since, until)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"could not parse time window: {exc}"
        ) from exc

    rows = select_runs_in_window(window, target_url=url, output_root=output_root)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no runs found in window "
                f"[{window.start.isoformat()}, {window.end.isoformat()}) "
                f"for url={url!r}"
            ),
        )
    if len(rows) == 1:
        # Single run in window — emit empty diff.
        payload: dict[str, Any] = {
            "run_id_a": rows[0]["run_id"],
            "run_id_b": rows[0]["run_id"],
            "added": [],
            "removed": [],
            "severity_changed": [],
            "score_changed": [],
            "summary": "only one run in window; emitting empty diff",
            "warning": "only_one_run",
        }
        return JSONResponse(payload)

    earliest = rows[0]
    latest = rows[-1]
    previous = load_sub_reports_for_run(str(earliest["run_id"]), output_root)
    current = load_sub_reports_for_run(str(latest["run_id"]), output_root)
    payload = compute_diff(
        run_id_a=str(earliest["run_id"]),
        run_id_b=str(latest["run_id"]),
        sub_reports_a=previous or {},
        sub_reports_b=current or {},
    )
    return JSONResponse(payload)


__all__ = ["router"]
