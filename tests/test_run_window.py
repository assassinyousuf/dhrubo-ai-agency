"""Tests for :mod:`dhrubo.core.run_window`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dhrubo.core.run_window import select_runs_in_window
from dhrubo.core.timeparse import Window


def _write_index(run_dir: Path, rows: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "index.json").write_text(json.dumps(rows), encoding="utf-8")


def _row(ts: str, *, target_url: str = "https://example.com/", seed_domain: str = "example.com") -> dict:
    return {
        "run_id": f"{ts}_{seed_domain}",
        "ts": ts,
        "target_url": target_url,
        "target_urls": [target_url],
        "seed_domain": seed_domain,
        "n_pages": 1,
        "sub_reports_path": f"output/{ts}_{seed_domain}/data.json",
        "pages_json_path": None,
        "diff_against": None,
    }


# A fixed-window baseline for tests.
_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def test_select_empty_index_returns_empty(tmp_path: Path) -> None:
    win = Window(_NOW - timedelta(days=7), _NOW)
    assert select_runs_in_window(win, target_url=None, output_root=tmp_path) == []


def test_select_filters_by_ts_window(tmp_path: Path) -> None:
    _write_index(
        tmp_path / "20260620T120000Z_example.com",
        [_row("20260620T120000Z")],
    )
    _write_index(
        tmp_path / "20260628T120000Z_example.com",
        [_row("20260628T120000Z")],
    )
    # Half-open window: a row at exactly ``end`` is excluded.
    _write_index(
        tmp_path / "20260701T120000Z_example.com",
        [_row("20260701T120000Z")],
    )
    win = Window(_NOW - timedelta(days=7), _NOW + timedelta(seconds=1))
    rows = select_runs_in_window(win, target_url=None, output_root=tmp_path)
    assert [r["ts"] for r in rows] == ["20260628T120000Z", "20260701T120000Z"]


def test_select_sorts_ascending(tmp_path: Path) -> None:
    # Out-of-order indexes on disk — selector should still emit
    # earliest-first.
    _write_index(tmp_path / "20260701T120000Z_example.com", [_row("20260701T120000Z")])
    _write_index(tmp_path / "20260601T120000Z_example.com", [_row("20260601T120000Z")])
    _write_index(tmp_path / "20260615T120000Z_example.com", [_row("20260615T120000Z")])
    win = Window(_NOW - timedelta(days=30), _NOW + timedelta(hours=1))
    rows = select_runs_in_window(win, target_url=None, output_root=tmp_path)
    assert [r["ts"] for r in rows] == [
        "20260601T120000Z",
        "20260615T120000Z",
        "20260701T120000Z",
    ]


def test_select_filters_by_target_url(tmp_path: Path) -> None:
    _write_index(
        tmp_path / "20260701T120000Z_example.com",
        [_row("20260701T120000Z", target_url="https://example.com/")],
    )
    _write_index(
        tmp_path / "20260701T120100Z_other.com",
        [_row("20260701T120100Z", target_url="https://other.com/", seed_domain="other.com")],
    )
    win = Window(_NOW - timedelta(hours=1), _NOW + timedelta(hours=1))
    rows = select_runs_in_window(
        win, target_url="https://example.com/", output_root=tmp_path
    )
    assert len(rows) == 1
    assert rows[0]["seed_domain"] == "example.com"


def test_select_filters_by_seed_domain(tmp_path: Path) -> None:
    """When the user passes a host, the selector should also match
    rows where ``seed_domain`` equals it (multi-page runs)."""
    _write_index(
        tmp_path / "20260701T120000Z_example.com",
        [_row("20260701T120000Z", seed_domain="example.com")],
    )
    _write_index(
        tmp_path / "20260701T120100Z_other.com",
        [_row("20260701T120100Z", seed_domain="other.com")],
    )
    win = Window(_NOW - timedelta(hours=1), _NOW + timedelta(hours=1))
    rows = select_runs_in_window(
        win, target_url="example.com", output_root=tmp_path
    )
    assert len(rows) == 1
    assert rows[0]["seed_domain"] == "example.com"


def test_select_window_excludes_endpoints(tmp_path: Path) -> None:
    """Window is half-open ``[start, end)`` — a row at exactly
    ``end`` is excluded."""
    _write_index(
        tmp_path / "20260701T120000Z_example.com",
        [_row("20260701T120000Z")],
    )
    win = Window(_NOW - timedelta(hours=1), _NOW)
    rows = select_runs_in_window(win, target_url=None, output_root=tmp_path)
    assert rows == []  # row at exactly ``end`` is excluded


def test_select_skips_malformed_rows(tmp_path: Path) -> None:
    """Rows missing ``ts`` or with a non-string ``ts`` are silently
    skipped (defensive)."""
    bad = tmp_path / "20260701T120000Z_example.com"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "index.json").write_text(
        json.dumps(
            [
                {"run_id": "x", "ts": 12345},  # non-string
                {"run_id": "y"},  # missing
                {"run_id": "z", "ts": "not-a-timestamp"},
            ]
        ),
        encoding="utf-8",
    )
    good = tmp_path / "20260701T120100Z_example.com"
    good.mkdir(parents=True, exist_ok=True)
    (good / "index.json").write_text(
        json.dumps([_row("20260701T120100Z")]), encoding="utf-8"
    )
    win = Window(_NOW - timedelta(hours=1), _NOW + timedelta(hours=1))
    rows = select_runs_in_window(win, target_url=None, output_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["ts"] == "20260701T120100Z"
