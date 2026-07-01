"""Tests for :mod:`dhrubo.tools.diff_tool`."""

from __future__ import annotations

import asyncio

from dhrubo.tools.diff_tool import DiffParams, DiffTool
from dhrubo.tools.tool_interface import ToolContext


def _run(tool: DiffTool, params: DiffParams) -> dict:
    res = asyncio.run(tool.run(params, ToolContext(requester_role="test")))
    assert res.success, res.error
    return res.data or {}


def _seo(issues: list[dict], score: int = 80) -> dict:
    return {"score": score, "summary": "ok", "issues": issues}


def _issue(
    id_: str,
    *,
    severity: str = "major",
    title: str = "Sample issue",
    detail: str = "detail",
) -> dict:
    return {
        "id": id_,
        "severity": severity,
        "title": title,
        "detail": detail,
        "recommendation": "fix it",
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_diff_empty_when_no_changes() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={"seo_report": _seo([_issue("x:11112222")])},
            sub_reports_b={"seo_report": _seo([_issue("x:11112222")])},
        ),
    )
    assert data["added"] == []
    assert data["removed"] == []
    assert data["severity_changed"] == []
    assert data["score_changed"] == []
    assert data["summary"] == "0 added, 0 removed, 0 severity-changed, 0 score-changed"


def test_diff_added_issue() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo([_issue("missing-meta:abc")]),
            },
            sub_reports_b={
                "seo_report": _seo(
                    [
                        _issue("missing-meta:abc"),
                        _issue("h1-missing:99999999", title="H1 missing"),
                    ]
                ),
            },
        ),
    )
    assert len(data["added"]) == 1
    assert data["added"][0]["lens"] == "seo_report"
    assert data["added"][0]["issue"]["title"] == "H1 missing"
    assert data["removed"] == []


def test_diff_removed_issue() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo(
                    [
                        _issue("missing-meta:abc"),
                        _issue("h1-missing:99999999", title="H1 missing"),
                    ]
                ),
            },
            sub_reports_b={
                "seo_report": _seo([_issue("missing-meta:abc")]),
            },
        ),
    )
    assert len(data["removed"]) == 1
    assert data["removed"][0]["issue"]["title"] == "H1 missing"


def test_diff_severity_changed() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo(
                    [_issue("missing-meta:abc", severity="major", title="Missing meta")]
                ),
            },
            sub_reports_b={
                "seo_report": _seo(
                    [_issue("missing-meta:abc", severity="minor", title="Missing meta")]
                ),
            },
        ),
    )
    assert len(data["severity_changed"]) == 1
    row = data["severity_changed"][0]
    assert row["severity_a"] == "major"
    assert row["severity_b"] == "minor"
    assert row["title"] == "Missing meta"


def test_diff_score_changed() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo([_issue("x:11112222")], score=80),
                "security_report": _seo([], score=70),
            },
            sub_reports_b={
                "seo_report": _seo([_issue("x:11112222")], score=75),
                "security_report": _seo([], score=70),
            },
        ),
    )
    assert len(data["score_changed"]) == 1
    assert data["score_changed"][0]["lens"] == "seo_report"
    assert data["score_changed"][0]["score_a"] == 80
    assert data["score_changed"][0]["score_b"] == 75
    assert data["score_changed"][0]["delta"] == -5


# ---------------------------------------------------------------------------
# Identity: id-first, fallback to (severity, title, detail)
# ---------------------------------------------------------------------------


def test_diff_uses_id_when_present() -> None:
    # Same title, different id → counts as added/removed.
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo(
                    [_issue("issue:aaa", title="Same title", detail="old")]
                ),
            },
            sub_reports_b={
                "seo_report": _seo(
                    [_issue("issue:bbb", title="Same title", detail="new")]
                ),
            },
        ),
    )
    assert len(data["added"]) == 1
    assert len(data["removed"]) == 1


def test_diff_falls_back_to_severity_title_detail() -> None:
    # Both sides lack `id`; identity is (severity, title, detail).
    a = {
        "seo_report": _seo(
            [
                {
                    "severity": "major",
                    "title": "Bad meta",
                    "detail": "Same detail",
                    "recommendation": "fix",
                }
            ]
        ),
    }
    b = {
        "seo_report": _seo(
            [
                {
                    "severity": "minor",  # severity changed → still detected via fallback id
                    "title": "Bad meta",
                    "detail": "Same detail",
                    "recommendation": "fix",
                }
            ]
        ),
    }
    data = _run(DiffTool(), DiffParams(run_id_a="a", run_id_b="b", sub_reports_a=a, sub_reports_b=b))
    # The fallback id includes severity, so a severity change looks like remove+add.
    assert len(data["added"]) == 1
    assert len(data["removed"]) == 1


# ---------------------------------------------------------------------------
# Multi-page payload handling
# ---------------------------------------------------------------------------


def test_diff_handles_multi_page_namespacing() -> None:
    multi_a = {
        "0": {
            "seo_report": _seo([_issue("seo:page0")]),
            "security_report": _seo([_issue("sec:page0")]),
        },
        "1": {
            "seo_report": _seo([_issue("seo:page1")]),
            "security_report": _seo([], score=80),
        },
    }
    multi_b = {
        "0": {
            "seo_report": _seo(
                [
                    _issue("seo:page0"),
                    _issue("seo:page0-new", title="Page 0 new SEO issue"),
                ]
            ),
            "security_report": _seo([_issue("sec:page0")]),
        },
        "1": {
            "seo_report": _seo([_issue("seo:page1")]),
            "security_report": _seo([], score=70),  # score dropped on page 1
        },
    }
    data = _run(
        DiffTool(),
        DiffParams(run_id_a="a", run_id_b="b", sub_reports_a=multi_a, sub_reports_b=multi_b),
    )
    # 1 added on page 0, 1 score-changed on page 1.
    assert len(data["added"]) == 1
    assert data["added"][0]["page"] == "0"
    assert data["added"][0]["issue"]["title"] == "Page 0 new SEO issue"
    assert len(data["score_changed"]) == 1
    assert data["score_changed"][0]["page"] == "1"
    assert data["score_changed"][0]["delta"] == -10


def test_diff_handles_missing_payload() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={},
            sub_reports_b={},
        ),
    )
    assert data["added"] == []
    assert data["removed"] == []
    assert data["severity_changed"] == []
    assert data["score_changed"] == []
    assert "0 added" in data["summary"]


def test_diff_summary_count() -> None:
    data = _run(
        DiffTool(),
        DiffParams(
            run_id_a="a",
            run_id_b="b",
            sub_reports_a={
                "seo_report": _seo(
                    [
                        _issue("a:1", severity="critical", title="A"),
                        _issue("b:2", severity="minor", title="B"),
                    ],
                    score=50,
                ),
            },
            sub_reports_b={
                "seo_report": _seo(
                    [
                        _issue("a:1", severity="major", title="A"),  # severity changed
                        _issue("c:3", severity="info", title="C"),  # added
                    ],
                    score=70,  # score changed
                ),
            },
        ),
    )
    assert data["summary"] == "1 added, 1 removed, 1 severity-changed, 1 score-changed"
