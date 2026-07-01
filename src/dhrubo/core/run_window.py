"""`run_window` — select runs from the per-host run index (M11).

Wraps :func:`dhrubo.core.run_index.load_run_index` with a
time-window filter and an optional URL/host filter. Both
``run-audit --diff-since`` and the standalone ``dhrubo diff``
subcommand rely on this helper to pick the right pair of
runs to diff.

Filter semantics:

- **Time window.** Rows whose ``ts`` (parsed from the directory
  name) falls in ``[window.start, window.end)``. ``ts`` is the
  compact ISO 8601 string written by the exporter:
  ``YYYYMMDDTHHMMSSZ``.
- **URL filter.** When given, keep rows whose ``target_url``
  equals the filter OR whose ``seed_domain`` equals the filter
  (so ``--url https://example.com/`` and ``--url example.com``
  both match the same host).

Output is sorted ascending by ``ts`` so ``rows[0]`` is the
earliest and ``rows[-1]`` is the latest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dhrubo.core.run_index import load_run_index
from dhrubo.core.timeparse import Window

_TS_FORMAT = "%Y%m%dT%H%M%SZ"


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def select_runs_in_window(
    window: Window,
    *,
    target_url: str | None,
    output_root: Path,
) -> list[dict[str, Any]]:
    """Return rows in ``[window.start, window.end)`` matching the
    URL filter, sorted ascending by ``ts``.

    ``target_url`` is matched against both ``target_url`` and
    ``seed_domain`` for ergonomics (``--url example.com`` works
    for multi-page runs whose rows store ``seed_domain``).
    """
    rows = load_run_index(output_root)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("ts")
        if not isinstance(ts, str):
            continue
        parsed = _parse_ts(ts)
        if parsed is None:
            continue
        if not (window.start <= parsed < window.end):
            continue
        if target_url:
            ru = row.get("target_url")
            sd = row.get("seed_domain")
            if target_url not in (ru, sd):
                continue
        out.append(row)
    out.sort(key=lambda r: r.get("ts") or "")
    return out


__all__ = ["select_runs_in_window"]
