"""Tests for :mod:`dhrubo.core.timeparse`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dhrubo.core.timeparse import Window, parse_since, parse_window

_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Relative durations
# ---------------------------------------------------------------------------


def test_parse_relative_minutes() -> None:
    assert parse_since("30m", now=_NOW) == _NOW - timedelta(minutes=30)


def test_parse_relative_hours() -> None:
    assert parse_since("24h", now=_NOW) == _NOW - timedelta(hours=24)


def test_parse_relative_days() -> None:
    assert parse_since("7d", now=_NOW) == _NOW - timedelta(days=7)


def test_parse_relative_weeks() -> None:
    assert parse_since("1w", now=_NOW) == _NOW - timedelta(weeks=1)
    assert parse_since("2w", now=_NOW) == _NOW - timedelta(weeks=2)


def test_parse_relative_uppercase() -> None:
    assert parse_since("7D", now=_NOW) == _NOW - timedelta(days=7)


def test_parse_relative_with_whitespace() -> None:
    assert parse_since("  7d  ", now=_NOW) == _NOW - timedelta(days=7)


def test_parse_relative_zero_raises() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        parse_since("0d", now=_NOW)


def test_parse_relative_bad_unit() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        parse_since("7x", now=_NOW)


def test_parse_relative_missing_unit() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        parse_since("7", now=_NOW)


def test_parse_relative_negative_raises() -> None:
    """Negative durations are rejected — they're not a useful
    notion for "since <duration> ago"."""
    with pytest.raises(ValueError, match="could not parse"):
        parse_since("-7d", now=_NOW)


# ---------------------------------------------------------------------------
# Absolute dates
# ---------------------------------------------------------------------------


def test_parse_absolute_date() -> None:
    assert parse_since("2026-06-01", now=_NOW) == datetime(
        2026, 6, 1, 0, 0, 0, tzinfo=UTC
    )


def test_parse_absolute_date_with_time() -> None:
    assert parse_since("2026-06-01T12:30:00", now=_NOW) == datetime(
        2026, 6, 1, 12, 30, 0, tzinfo=UTC
    )


def test_parse_absolute_date_with_z_suffix() -> None:
    assert parse_since("2026-06-01T12:30:00Z", now=_NOW) == datetime(
        2026, 6, 1, 12, 30, 0, tzinfo=UTC
    )


def test_parse_absolute_date_lowercase_t() -> None:
    assert parse_since("2026-06-01t12:30:00z", now=_NOW) == datetime(
        2026, 6, 1, 12, 30, 0, tzinfo=UTC
    )


def test_parse_absolute_date_with_offset() -> None:
    # `+02:00` should be normalised to UTC.
    assert parse_since("2026-06-01T14:30:00+02:00", now=_NOW) == datetime(
        2026, 6, 1, 12, 30, 0, tzinfo=UTC
    )


def test_parse_absolute_date_bad() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        parse_since("not-a-date", now=_NOW)


def test_parse_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_since("", now=_NOW)


# ---------------------------------------------------------------------------
# parse_window
# ---------------------------------------------------------------------------


def test_parse_window_default_window_is_seven_days() -> None:
    win = parse_window(None, None, now=_NOW)
    assert win == Window(_NOW - timedelta(days=7), _NOW)


def test_parse_window_default_until_is_now() -> None:
    win = parse_window("7d", None, now=_NOW)
    assert win.start == _NOW - timedelta(days=7)
    assert win.end == _NOW


def test_parse_window_both_absolute() -> None:
    win = parse_window("2026-06-01", "2026-07-01", now=_NOW)
    assert win.start == datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    assert win.end == datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)


def test_parse_window_relative_on_until() -> None:
    win = parse_window("7d", "1d", now=_NOW)
    assert win.start == _NOW - timedelta(days=7)
    assert win.end == _NOW - timedelta(days=1)


def test_parse_window_until_before_since_raises() -> None:
    with pytest.raises(ValueError, match=r"start.*after end"):
        parse_window("2026-07-01", "2026-06-01", now=_NOW)
