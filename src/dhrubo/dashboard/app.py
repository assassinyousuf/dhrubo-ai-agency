"""`dhrubo.dashboard.app` — FastAPI app factory.

The factory takes ``output_root`` (where audit runs live) and
``config_dir`` (YAML configs). A single :class:`RunSupervisor`
is bound to ``app.state`` and shared by all routes.

We use the factory pattern (no module-level ``app``) so tests
can spin up isolated instances against ``tmp_path`` without
needing to reset module state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dhrubo.config.settings import Settings
from dhrubo.dashboard import routes as _routes  # noqa: F401  (registers routers)
from dhrubo.dashboard.paths import _resolve_static_dir
from dhrubo.dashboard.routes.diff import router as diff_router
from dhrubo.dashboard.routes.publish import router as publish_router
from dhrubo.dashboard.routes.runs import router as runs_router
from dhrubo.dashboard.routes.system import router as system_router
from dhrubo.dashboard.supervisor import RunSupervisor


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """No-op lifespan. The supervisor is created by the factory
    and torn down when the app process exits. Kept as a hook for
    future startup/shutdown work (DB pool, telemetry, …)."""
    yield


def create_app(
    *,
    output_root: Path,
    config_dir: Path,
    settings: Settings | None = None,
) -> FastAPI:
    """Build a FastAPI app instance bound to a specific run directory.

    Args:
        output_root: Where audit runs are written
            (``runs/<ts>_<host>/{report.md,data.json,diff.json,...}``).
        config_dir: YAML config directory
            (``config/{models,permissions,retry_policies,...}.yaml``).
        settings: Optional pre-built :class:`Settings` (used by
            tests to inject custom values; the CLI builds it
            from env+yaml).

    The :class:`RunSupervisor` is created here and exposed as
    ``app.state.supervisor``.
    """
    if settings is None:
        # Local import to avoid pulling pydantic-settings in tests
        # that don't need it.
        from dhrubo.config.settings import Settings as _Settings

        settings = _Settings()

    app = FastAPI(
        title="Dhrubo Dashboard",
        version="0.13.0",
        lifespan=_lifespan,
        # Loopback-only by design. We don't set CORS origins.
    )

    # Bind shared singletons onto app.state. Routes pull from here
    # rather than importing globals so tests can inject fixtures.
    supervisor = RunSupervisor(
        max_concurrent=settings.dashboard.max_concurrent_runs,
        cwd=Path.cwd(),
    )
    app.state.output_root = output_root
    app.state.config_dir = config_dir
    app.state.settings = settings
    app.state.supervisor = supervisor

    # Static assets (vanilla CSS/JS, no build step).
    static_dir = _resolve_static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Routers.
    app.include_router(system_router, tags=["system"])
    app.include_router(runs_router, tags=["runs"])
    app.include_router(diff_router, tags=["diff"])
    app.include_router(publish_router, tags=["publish"])

    return app


__all__ = ["create_app"]
