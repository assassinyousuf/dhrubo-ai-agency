"""`dhrubo.dashboard` — local web dashboard for the audit pipeline (M13).

Public surface:

- :func:`create_app` — FastAPI app factory.
- :class:`RunSupervisor` — asyncio-based process supervisor
  for ``dhrubo run-audit`` subprocesses.

Importing this package requires the ``[ui]`` extra to be
installed (``fastapi``, ``uvicorn``, ``sse-starlette``). The
:mod:`dhrubo.commands.cli` subcommand ``dhrubo dashboard``
defers those imports so the rest of the CLI stays usable
without the extra.
"""

from __future__ import annotations

__all__ = ["create_app", "RunSupervisor"]