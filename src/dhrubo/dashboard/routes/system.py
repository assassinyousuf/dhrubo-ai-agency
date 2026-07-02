"""`dhrubo.dashboard.routes.system` — health + version endpoints.

Tiny router. Exposes ``GET /healthz`` for CI scripts and the
browser console (it pings on page load to verify the server
is alive). Includes the resolved version + cwd so a remote
debugger can prove which checkout is actually serving.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    """Liveness probe. Returns the dashboard's resolved state."""
    settings = getattr(request.app.state, "settings", None)
    output_root: Path = getattr(request.app.state, "output_root", Path.cwd())
    supervisor = getattr(request.app.state, "supervisor", None)
    return {
        "ok": True,
        "version": "0.13.0",
        "cwd": str(Path.cwd()),
        "output_root": str(output_root),
        "max_concurrent_runs": (
            settings.dashboard.max_concurrent_runs if settings else None
        ),
        "running_jobs": len(supervisor.running_jobs()) if supervisor else 0,
    }


__all__ = ["router"]
