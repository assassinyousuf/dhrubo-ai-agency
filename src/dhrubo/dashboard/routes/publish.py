"""`dhrubo.dashboard.routes.publish` — publish page + JSON endpoint.

Mirrors the ``dhrubo publish`` CLI subcommand's surface. The
GitHub token is read from the form body (per-request),
passed to :class:`PublisherAgent`, and immediately discarded.
It is never logged, never persisted, never echoed in the
response.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.publisher import PublisherAgent
from dhrubo.config.settings import Settings
from dhrubo.core.run_index import load_run_index
from dhrubo.dashboard.paths import _resolve_template_dir

router = APIRouter()
_templates = Jinja2Templates(directory=str(_resolve_template_dir()))


def _list_recent_diff_paths(output_root: Path) -> list[str]:
    """Enumerate ``<run_dir>/diff.json`` paths for the publish form.

    Used as a quick-pick dropdown on the publish page so users
    don't have to type the path. Sorted by ``run_id`` (which
    is also a sortable timestamp) descending.
    """
    paths: list[str] = []
    for row in load_run_index(output_root):
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        candidate = output_root / run_id / "diff.json"
        if candidate.exists():
            paths.append(str(candidate))
    paths.sort(reverse=True)
    return paths[:25]


@router.get("/publish", response_class=HTMLResponse)
async def publish_form(request: Request) -> Response:
    """Render the publish form."""
    output_root = Path(request.app.state.output_root)
    settings: Settings = request.app.state.settings
    return _templates.TemplateResponse(
        request,
        "publish.html",
        {
            "recent_diffs": _list_recent_diff_paths(output_root),
            "api_key_env": settings.github.api_key_env,
            "repository_env": settings.github.repository_env,
        },
    )


@router.post("/api/publish")
async def publish_submit(
    request: Request,
    diff_path: str = Form(default=""),
    repo: str | None = Form(default=None),
    pr_number: int | None = Form(default=None),
    github_token: str | None = Form(default=None),
    max_issues_per_lens: int = Form(default=50),
) -> Response:
    """Validate + call :class:`PublisherAgent` in-process."""
    # ---- Validate inputs (same checks the M12 CLI does) ----
    settings: Settings = request.app.state.settings
    if not diff_path:
        raise HTTPException(status_code=400, detail="'diff_path' is required")
    diff_p = Path(diff_path)
    if not diff_p.exists() or not diff_p.is_file():
        raise HTTPException(
            status_code=400, detail=f"diff_path does not exist: {diff_path}"
        )
    repo = (repo or os.environ.get(settings.github.repository_env) or "").strip()
    if not repo:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'repo' is required (or set the {settings.github.repository_env} "
                f"env var)"
            ),
        )
    if "/" not in repo or repo.count("/") != 1:
        raise HTTPException(
            status_code=400, detail=f"repo must be of the form 'owner/name', got {repo!r}"
        )
    token = (github_token or os.environ.get(settings.github.api_key_env) or "").strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'github_token' is required (or set the "
                f"{settings.github.api_key_env} env var)"
            ),
        )
    if pr_number is None or pr_number < 1:
        raise HTTPException(
            status_code=400, detail="'pr_number' must be a positive integer"
        )

    # ---- Run the publisher in-process ----
    publisher = PublisherAgent()
    ctx = AgentContext(
        role=publisher.role,
        inputs={
            "diff_path": diff_p,
            "repo": repo,
            "pr_number": pr_number,
            "github_token": token,
            "max_issues_per_lens": max_issues_per_lens,
        },
    )
    result = await publisher.safe_execute(ctx)

    # Drop the token reference from the local scope before
    # any branch of the response path runs.
    del token, github_token

    if not result.success:
        return JSONResponse(
            {"ok": False, "error": result.error or "publish failed"},
            status_code=400,
        )
    body: dict[str, Any] = {
        "ok": True,
        "comment_url": result.outputs.get("comment_url"),
        "comment_id": result.outputs.get("comment_id"),
        "repo": result.outputs.get("repo"),
        "pr_number": result.outputs.get("pr_number"),
    }
    return JSONResponse(body)


__all__ = ["router"]
