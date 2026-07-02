"""`dhrubo.dashboard.routes` — HTTP routers for the dashboard.

Importing this package registers all four routers. The
:mod:`dhrubo.dashboard.app` factory imports this package for
its side-effects (the routers attach to a freshly-created
``FastAPI`` instance via :func:`app.include_router`).
"""

from __future__ import annotations

__all__: list[str] = []
