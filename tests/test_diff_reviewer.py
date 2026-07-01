"""Tests for :mod:`dhrubo.agents.diff_reviewer`."""

from __future__ import annotations

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.diff_reviewer import DiffReviewerAgent
from dhrubo.tools.diff_tool import DiffTool


def _agent() -> DiffReviewerAgent:
    return DiffReviewerAgent()


async def test_diff_reviewer_calls_tool_with_inputs() -> None:
    agent = _agent()
    previous = {
        "seo_report": {"score": 80, "issues": []},
        "security_report": {"score": 70, "issues": []},
    }
    current = {
        "seo_report": {
            "score": 80,
            "issues": [
                {
                    "id": "missing-meta:abc",
                    "severity": "major",
                    "title": "Missing meta",
                    "detail": "…",
                    "recommendation": "…",
                }
            ],
        },
        "security_report": {"score": 70, "issues": []},
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "previous_sub_reports": previous,
            "sub_reports": current,
            "diff_against": "20260702T000000Z_example.com",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    diff = res.outputs["diff_payload"]
    assert diff["run_id_a"] == "20260702T000000Z_example.com"
    assert diff["run_id_b"] == "current"
    assert len(diff["added"]) == 1
    assert diff["added"][0]["lens"] == "seo_report"


async def test_diff_reviewer_emits_diff_payload() -> None:
    agent = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "previous_sub_reports": {
                "seo_report": {"score": 80, "issues": []},
            },
            "sub_reports": {
                "seo_report": {"score": 75, "issues": []},
            },
            "diff_against": "p",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    diff = res.outputs["diff_payload"]
    assert "summary" in diff
    assert "added" in diff
    assert "removed" in diff
    assert "severity_changed" in diff
    assert "score_changed" in diff


async def test_diff_reviewer_handles_missing_previous() -> None:
    """When ``previous_sub_reports`` is missing, the diff is over an
    empty baseline — every issue on the current side shows as 'added'."""
    agent = _agent()
    current = {
        "seo_report": {
            "score": 80,
            "issues": [
                {
                    "id": "a:1",
                    "severity": "major",
                    "title": "X",
                    "detail": "d",
                    "recommendation": "r",
                }
            ],
        },
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "previous_sub_reports": {},
            "sub_reports": current,
            "diff_against": "p",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    diff = res.outputs["diff_payload"]
    assert len(diff["added"]) == 1
    assert diff["added"][0]["issue"]["id"] == "a:1"


async def test_diff_reviewer_handles_tool_failure() -> None:
    """When the tool blows up, the agent returns a successful empty
    diff_payload (never fails the pipeline)."""
    agent = DiffReviewerAgent(diff_tool=_BrokenDiffTool())
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "previous_sub_reports": {"seo_report": {"score": 80, "issues": []}},
            "sub_reports": {"seo_report": {"score": 80, "issues": []}},
            "diff_against": "p",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    diff = res.outputs["diff_payload"]
    assert diff["added"] == []
    assert diff["removed"] == []
    assert "diff unavailable" in diff["summary"]


class _BrokenDiffTool(DiffTool):
    """DiffTool stub that always fails — used to exercise the
    failure-handling path in DiffReviewerAgent."""

    async def _do_call(self, **_kwargs):  # type: ignore[override]
        raise RuntimeError("simulated diff failure")
