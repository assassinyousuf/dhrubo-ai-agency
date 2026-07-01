"""`run_index` — read the per-host audit run index from disk (M10/M11).

Pure filesystem helpers. Lives in ``core/`` so that other core
modules (``run_window``, future retention policy) can build on
it without dragging in the ``agents.exporter`` import chain.
``agents.exporter`` re-exports these symbols for backward compat
with the M10 CLI/tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dhrubo.core.logger import get_logger

_log = get_logger("core.run_index")


def load_run_index(output_root: Path) -> list[dict[str, Any]]:
    """Read every ``<run_dir>/index.json`` under ``output_root``.

    Returns the union of rows across all per-host indexes. Used by
    the CLI's ``--diff-against`` resolver (M10) and the
    ``--diff-since`` / standalone ``diff`` subcommand (M11).
    """
    rows: list[dict[str, Any]] = []
    if not output_root.exists():
        return rows
    for path in output_root.glob("*/index.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list):
            rows.extend(r for r in data if isinstance(r, dict))
    return rows


def load_sub_reports_for_run(run_id: str, output_root: Path) -> dict[str, Any] | None:
    """Resolve a ``run_id`` to its structured sub-reports dict.

    Walks every per-host index, looks up the matching row, and
    reads its ``sub_reports_path``. Tries the stored path verbatim
    first (in case it was absolute), then falls back to joining it
    with ``output_root``.
    """
    for row in load_run_index(output_root):
        if row.get("run_id") != run_id:
            continue
        rel = row.get("sub_reports_path")
        if not rel:
            return None
        path = Path(rel)
        if not path.exists() and not path.is_absolute():
            path = output_root / rel
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data.get("sub_reports") if isinstance(data, dict) else None
    return None


__all__ = ["load_run_index", "load_sub_reports_for_run"]
