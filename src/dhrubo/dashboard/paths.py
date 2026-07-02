"""`dhrubo.dashboard.paths` — package-internal path resolvers.

Split out of :mod:`dhrubo.dashboard.app` so the routes can import
``_resolve_template_dir`` without forming a circular dependency
``app -> routes -> app``. These helpers depend on nothing but
``pathlib`` and the package layout.
"""

from __future__ import annotations

from pathlib import Path


def _resolve_static_dir() -> Path:
    """Return the path to the bundled static/ directory."""
    return Path(__file__).parent / "static"


def _resolve_template_dir() -> Path:
    """Return the path to the bundled templates/ directory."""
    return Path(__file__).parent / "templates"


__all__ = ["_resolve_static_dir", "_resolve_template_dir"]
