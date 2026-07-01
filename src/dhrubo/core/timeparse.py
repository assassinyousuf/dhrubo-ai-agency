"""`timeparse` — parse human-friendly time windows (M11).

Supports two input shapes:

1. **Relative** — ``<int><unit>`` where ``unit`` is one of
   ``m`` (minute), ``h`` (hour), ``d`` (day), ``w`` (week).
   Examples: ``7d``, ``24h``, ``1w``, ``30m``.

2. **Absolute** — ISO 8601:
   - Date only: ``YYYY-MM-DD`` (interpreted as ``00:00:00`` UTC).
   - Date + time: ``YYYY-MM-DDTHH:MM:SS`` (UTC).
   - Date + time + Z: ``YYYY-MM-DDTHH:MM:SSZ`` (UTC).

The public API is :func:`parse_since` (one anchor) and
:func:`parse_window` (a ``(start, end)`` pair). Both return
timezone-aware ``datetime`` objects in UTC.

Used by ``run-audit --diff-since`` and the standalone
``dhrubo diff`` subcommand to resolve time windows over the
per-host run index.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

_RELATIVE_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_ABSOLUTE_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_ABSOLUTE_DT_RE = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:[Zz]|[+-]\d{2}:?\d{2})?\s*$"
)

# Unit -> timedelta kwargs. Weeks are converted to days to keep
# `timedelta` arithmetic consistent across calendars.
_UNIT_TO_DAYS: dict[str, int] = {"w": 7, "d": 1}
_UNIT_HOURS: dict[str, int] = {"h": 1}
_UNIT_MINUTES: dict[str, int] = {"m": 1}


class Window(NamedTuple):
    """A closed-open ``[start, end)`` time window in UTC."""

    start: datetime
    end: datetime


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a single time anchor.

    Relative values are interpreted as ``now - <duration>``.
    Absolute values are returned as-is (UTC).

    Raises :class:`ValueError` with a friendly message on bad input.
    """
    if not value or not value.strip():
        raise ValueError("time value is empty")
    anchor = (now or datetime.now(tz=UTC)).astimezone(UTC)
    rel = _RELATIVE_RE.match(value)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2).lower()
        if unit in _UNIT_TO_DAYS:
            delta = timedelta(days=n * _UNIT_TO_DAYS[unit])
        elif unit in _UNIT_HOURS:
            delta = timedelta(hours=n * _UNIT_HOURS[unit])
        elif unit in _UNIT_MINUTES:
            delta = timedelta(minutes=n * _UNIT_MINUTES[unit])
        else:  # pragma: no cover — guarded by regex
            raise ValueError(f"unknown unit {unit!r} in {value!r}")
        if n <= 0:
            raise ValueError(f"relative duration must be positive (got {value!r})")
        return anchor - delta
    if _ABSOLUTE_DATE_RE.match(value):
        return _parse_iso_date(value)
    if _ABSOLUTE_DT_RE.match(value):
        return _parse_iso_datetime(value)
    raise ValueError(
        f"could not parse {value!r} "
        "(expected '<int><m|h|d|w>' or 'YYYY-MM-DD' or "
        "'YYYY-MM-DDTHH:MM:SS[Z]')"
    )


def parse_window(
    since: str | None,
    until: str | None = None,
    *,
    now: datetime | None = None,
) -> Window:
    """Parse a ``(since, until)`` pair into a :class:`Window`.

    ``until`` defaults to "now" when omitted. Both anchors are
    parsed via :func:`parse_since` (so relative values also work
    on ``until`` — e.g. ``--until 1h`` means "until one hour ago").

    Raises :class:`ValueError` when ``since > until``.
    """
    anchor = (now or datetime.now(tz=UTC)).astimezone(UTC)
    start = parse_since(since, now=anchor) if since else anchor - timedelta(days=7)
    end = parse_since(until, now=anchor) if until else anchor
    if start > end:
        raise ValueError(
            f"window start ({start.isoformat()}) is after end ({end.isoformat()})"
        )
    return Window(start=start, end=end)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_iso_date(value: str) -> datetime:
    raw = value.strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid date {raw!r}: {exc}") from exc
    return dt.replace(tzinfo=UTC)


def _parse_iso_datetime(value: str) -> datetime:
    raw = value.strip()
    # Normalise: ``fromisoformat`` is case-sensitive on the
    # separator and timezone marker. Uppercase both, then replace
    # ``Z`` with ``+00:00`` for the offset form.
    normalised = raw
    if "t" in normalised[:11]:
        normalised = normalised[:10] + "T" + normalised[11:]
    if normalised.endswith(("Z", "z")):
        normalised = normalised[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(f"invalid datetime {raw!r}: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


__all__ = ["Window", "parse_since", "parse_window"]
