"""Tests for :mod:`dhrubo.tools.markdown_diff_renderer` (M12).

Pure-function renderer — no httpx, no I/O. The renderer takes
a diff payload (as produced by :class:`DiffTool` /
:func:`compute_diff`) and emits a single Markdown body suitable
for posting to a GitHub PR comment.
"""

from __future__ import annotations

from typing import Any

import pytest
from dhrubo.tools.markdown_diff_renderer import render_diff_comment


def _empty_diff() -> dict[str, Any]:
    return {
        "run_id_a": "previous",
        "run_id_b": "current",
        "added": [],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
        "summary": "0 added, 0 removed, 0 severity-changed, 0 score-changed",
    }


# ---------------------------------------------------------------------------
# Empty / no-op
# ---------------------------------------------------------------------------


def test_renders_empty_diff() -> None:
    body = render_diff_comment(_empty_diff())
    assert body.startswith("## Website Audit Diff")
    assert "Comparing `previous` -> `current`" in body
    assert "_No structural changes._" in body
    # No per-lens table on empty diff.
    assert "| Lens | Added | Removed | Score Δ |" not in body


def test_renders_empty_diff_no_details_blocks() -> None:
    body = render_diff_comment(_empty_diff())
    assert "<details>" not in body


# ---------------------------------------------------------------------------
# Header + summary
# ---------------------------------------------------------------------------


def test_renders_header_with_run_ids() -> None:
    diff = _empty_diff()
    diff["run_id_a"] = "20260101T000000Z_example.com"
    diff["run_id_b"] = "20260108T000000Z_example.com"
    body = render_diff_comment(diff)
    assert "## Website Audit Diff" in body
    assert "Comparing `20260101T000000Z_example.com` -> `20260108T000000Z_example.com`" in body


def test_renders_summary_line() -> None:
    diff = _empty_diff()
    diff["summary"] = "5 added, 2 removed, 1 severity-changed, 0 score-changed"
    body = render_diff_comment(diff)
    assert "_5 added, 2 removed, 1 severity-changed, 0 score-changed_" in body


# ---------------------------------------------------------------------------
# Per-lens table
# ---------------------------------------------------------------------------


def test_renders_per_lens_table() -> None:
    diff = _empty_diff()
    diff["added"] = [
        {"lens": "seo_report", "page": None, "issue": {"id": "x:1", "severity": "minor", "title": "T"}},
        {"lens": "seo_report", "page": None, "issue": {"id": "x:2", "severity": "minor", "title": "T"}},
        {"lens": "security_report", "page": None, "issue": {"id": "x:3", "severity": "critical", "title": "T"}},
    ]
    diff["removed"] = [
        {"lens": "ui_report", "page": None, "issue": {"id": "x:4", "severity": "info", "title": "T"}},
    ]
    diff["score_changed"] = [
        {"lens": "seo_report", "page": None, "score_a": 80, "score_b": 75, "delta": -5},
        {"lens": "security_report", "page": None, "score_a": 70, "score_b": 70, "delta": 0},
    ]
    body = render_diff_comment(diff)
    assert "| Lens | Added | Removed | Score Δ |" in body
    assert "|---|---:|---:|---:|" in body
    # SEO row: 2 added, 0 removed, -5 score.
    assert "| SEO | 2 | 0 | -5 |" in body
    # UI row: 0 added, 1 removed, em-dash score (sum is zero).
    assert "| UI | 0 | 1 | — |" in body
    # Security row: 1 added, 0 removed, 0 score_delta -> positive sign "0"?
    # score_delta is 0 -> falsy -> the renderer prints the em-dash.
    assert "| Security | 1 | 0 | — |" in body


def test_renders_per_lens_table_with_positive_score_delta() -> None:
    diff = _empty_diff()
    diff["score_changed"] = [
        {"lens": "seo_report", "page": None, "score_a": 70, "score_b": 80, "delta": 10},
    ]
    body = render_diff_comment(diff)
    assert "| SEO | 0 | 0 | +10 |" in body


# ---------------------------------------------------------------------------
# Added / removed / severity-changed details blocks
# ---------------------------------------------------------------------------


def test_renders_added_issue_in_details_block() -> None:
    diff = _empty_diff()
    diff["added"] = [
        {
            "lens": "seo_report",
            "page": None,
            "issue": {
                "id": "missing-meta-description:abc12345",
                "severity": "major",
                "title": "Missing meta description",
                "detail": "The page lacks a meta description.",
                "recommendation": "Add a concise meta description.",
            },
        }
    ]
    body = render_diff_comment(diff)
    assert "<details>" in body
    assert "SEO (1 change)" in body  # 'change' (singular)
    assert "**Added (1)**" in body
    assert "`major`" in body
    assert "**Missing meta description**" in body
    assert "(`missing-meta-description:abc12345`)" in body


def test_renders_added_issue_plural_change_word() -> None:
    diff = _empty_diff()
    diff["added"] = [
        {"lens": "seo_report", "page": None, "issue": {"id": "x:1", "severity": "info", "title": "A"}},
        {"lens": "seo_report", "page": None, "issue": {"id": "x:2", "severity": "info", "title": "B"}},
    ]
    body = render_diff_comment(diff)
    assert "SEO (2 changes)" in body


def test_renders_removed_issue_in_details_block() -> None:
    diff = _empty_diff()
    diff["removed"] = [
        {
            "lens": "security_report",
            "page": None,
            "issue": {
                "id": "missing-csp:deadbeef",
                "severity": "critical",
                "title": "Missing Content-Security-Policy",
                "detail": "…",
                "recommendation": "…",
            },
        }
    ]
    body = render_diff_comment(diff)
    assert "**Removed (1)**" in body
    assert "`critical`" in body
    assert "**Missing Content-Security-Policy**" in body
    assert "(`missing-csp:deadbeef`)" in body


def test_renders_severity_changed() -> None:
    diff = _empty_diff()
    diff["severity_changed"] = [
        {
            "lens": "seo_report",
            "page": None,
            "id": "missing-h1:12345678",
            "title": "Page missing H1",
            "severity_a": "minor",
            "severity_b": "critical",
        }
    ]
    body = render_diff_comment(diff)
    assert "**Severity changed (1)**" in body
    assert "`minor` -> `critical`" in body
    assert "**Page missing H1**" in body
    assert "(`missing-h1:12345678`)" in body


# ---------------------------------------------------------------------------
# Truncation (capped at max_issues_per_lens)
# ---------------------------------------------------------------------------


def test_caps_issues_per_lens() -> None:
    diff = _empty_diff()
    # 60 added in seo_report.
    diff["added"] = [
        {
            "lens": "seo_report",
            "page": None,
            "issue": {"id": f"x:{i}", "severity": "minor", "title": f"T{i}"},
        }
        for i in range(60)
    ]
    body = render_diff_comment(diff, max_issues_per_lens=10)
    # Exactly 10 issue bullets, plus the truncation marker.
    seo_block = body.split("**Added (60)**", 1)[1].split("</details>", 1)[0]
    bullets = [ln for ln in seo_block.splitlines() if ln.startswith("- `")]
    assert len(bullets) == 10
    # Truncation marker present, mentions overflow count (50 left out).
    assert "…and 50 more added (truncated" in body


def test_caps_issues_per_lens_zero_disables_per_issue_list() -> None:
    """``max_issues_per_lens=0`` keeps the summary table but emits
    no per-issue bullets."""
    diff = _empty_diff()
    diff["added"] = [
        {"lens": "seo_report", "page": None, "issue": {"id": f"x:{i}", "severity": "minor", "title": f"T{i}"}}
        for i in range(5)
    ]
    body = render_diff_comment(diff, max_issues_per_lens=0)
    # Table still there.
    assert "| SEO | 5 | 0 |" in body
    # No per-issue bullets in the details block.
    assert "T0" not in body
    assert "T4" not in body


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_renders_when_no_summary_field() -> None:
    """Missing ``summary`` key is handled (renderer falls back to no italic line)."""
    diff: dict[str, Any] = {
        "run_id_a": "a",
        "run_id_b": "b",
        "added": [],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
    }
    body = render_diff_comment(diff)
    assert "## Website Audit Diff" in body
    # No italic summary line (because the field is absent).
    assert "\n_\n" not in body


def test_renders_when_run_id_keys_missing() -> None:
    diff: dict[str, Any] = {
        "added": [],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
        "summary": "no ids",
    }
    body = render_diff_comment(diff)
    assert "Comparing `?` -> `?`" in body


def test_negative_max_issues_per_lens_is_clamped_to_zero() -> None:
    diff = _empty_diff()
    diff["added"] = [
        {"lens": "seo_report", "page": None, "issue": {"id": "x:1", "severity": "minor", "title": "T"}}
    ]
    body = render_diff_comment(diff, max_issues_per_lens=-5)
    # Should behave like 0: no per-issue bullets.
    assert "**T**" not in body


def test_severity_ordering_in_details() -> None:
    """Within a single lens, added/removed rows are sorted by
    severity rank (critical -> info) then by id."""
    diff = _empty_diff()
    diff["added"] = [
        {"lens": "seo_report", "page": None, "issue": {"id": "info:1", "severity": "info", "title": "I"}},
        {"lens": "seo_report", "page": None, "issue": {"id": "crit:1", "severity": "critical", "title": "C"}},
        {"lens": "seo_report", "page": None, "issue": {"id": "major:1", "severity": "major", "title": "M"}},
    ]
    body = render_diff_comment(diff)
    seo = body.split("**Added (3)**", 1)[1].split("</details>", 1)[0]
    crit_idx = seo.find("**C**")
    major_idx = seo.find("**M**")
    info_idx = seo.find("**I**")
    assert 0 <= crit_idx < major_idx < info_idx


@pytest.mark.parametrize(
    "lens,title",
    [
        ("seo_report", "SEO"),
        ("ui_report", "UI"),
        ("performance_report", "Performance"),
        ("a11y_report", "Accessibility"),
        ("security_report", "Security"),
        ("branding_report", "Branding"),
    ],
)
def test_per_lens_title_renders_correctly(lens: str, title: str) -> None:
    diff = _empty_diff()
    diff["added"] = [
        {"lens": lens, "page": None, "issue": {"id": "x:1", "severity": "minor", "title": "T"}}
    ]
    body = render_diff_comment(diff)
    assert f"{title} (1 change)" in body
