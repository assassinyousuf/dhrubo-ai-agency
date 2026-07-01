"""Tests for :class:`dhrubo.agents.publisher.PublisherAgent` (M12).

The publisher is deterministic — no LLM call. We stub the inner
``GitHubCommentTool`` so the agent is exercised end-to-end without
any HTTP I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.publisher import PublisherAgent
from dhrubo.tools.github_comment_tool import GitHubCommentTool
from dhrubo.tools.tool_interface import ToolResult


def _diff_payload() -> dict[str, Any]:
    return {
        "run_id_a": "previous",
        "run_id_b": "current",
        "added": [
            {
                "lens": "seo_report",
                "page": None,
                "issue": {
                    "id": "missing-meta:abc12345",
                    "severity": "major",
                    "title": "Missing meta description",
                    "detail": "…",
                    "recommendation": "…",
                },
            }
        ],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
        "summary": "1 added, 0 removed, 0 severity-changed, 0 score-changed",
    }


class _StubGitHubCommentTool(GitHubCommentTool):
    """Records the params it was called with and returns a fixed success."""

    def __init__(self) -> None:
        # Skip parent's __init__ (we don't need a config dir or retry policy).
        self.captured: list[dict[str, Any]] = []
        self.next_result: ToolResult = ToolResult.ok(
            "github_comment",
            data={
                "comment_url": "https://github.com/foo/bar/pull/7#issuecomment-99",
                "id": 99,
                "repo": "foo/bar",
            },
            repo="foo/bar",
            pr_number=7,
            comment_url="https://github.com/foo/bar/pull/7#issuecomment-99",
        )

    async def safe_run(self, raw_params: dict[str, Any], ctx: Any) -> ToolResult:  # type: ignore[override]
        self.captured.append(raw_params)
        return self.next_result


def _agent() -> tuple[PublisherAgent, _StubGitHubCommentTool]:
    stub = _StubGitHubCommentTool()
    agent = PublisherAgent(github_comment_tool=stub)
    return agent, stub


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_publisher_emits_comment_url() -> None:
    agent, stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 7,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    assert res.outputs["comment_url"] == "https://github.com/foo/bar/pull/7#issuecomment-99"
    assert res.outputs["comment_id"] == 99
    assert res.outputs["repo"] == "foo/bar"
    assert res.outputs["pr_number"] == 7

    # The tool was called once with the right params.
    assert len(stub.captured) == 1
    sent = stub.captured[0]
    assert sent["repo"] == "foo/bar"
    assert sent["pr_number"] == 7
    assert sent["token"] == "secret"
    # Rendered body includes the diff header.
    assert "## Website Audit Diff" in sent["body"]
    assert "1 added" in sent["body"]


async def test_publisher_loads_diff_from_path(tmp_path: Path) -> None:
    """When ``diff_payload`` is absent, the agent reads ``diff_path`` from disk."""
    diff_path = tmp_path / "diff.json"
    diff_path.write_text(json.dumps(_diff_payload()), encoding="utf-8")
    agent, stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_path": diff_path,
            "repo": "foo/bar",
            "pr_number": 7,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    assert res.outputs["comment_url"].endswith("#issuecomment-99")


# ---------------------------------------------------------------------------
# Validation failures (no raise, success=False)
# ---------------------------------------------------------------------------


async def test_publisher_handles_missing_diff() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "repo": "foo/bar",
            "pr_number": 1,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "diff" in (res.error or "").lower()


async def test_publisher_handles_missing_diff_path_file(tmp_path: Path) -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_path": tmp_path / "does-not-exist.json",
            "repo": "foo/bar",
            "pr_number": 1,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "diff" in (res.error or "").lower()


async def test_publisher_handles_missing_repo() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "pr_number": 1,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "repo" in (res.error or "").lower()


async def test_publisher_handles_missing_token() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 1,
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "token" in (res.error or "").lower()


async def test_publisher_handles_missing_pr_number() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "pr_number" in (res.error or "").lower()


async def test_publisher_handles_zero_pr_number() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 0,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "pr_number" in (res.error or "").lower()


async def test_publisher_handles_non_int_pr_number() -> None:
    agent, _stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": "seven",
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "integer" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# Tool failure
# ---------------------------------------------------------------------------


async def test_publisher_handles_tool_failure() -> None:
    agent, stub = _agent()
    stub.next_result = ToolResult.fail("github_comment", error="HTTP 404: Not Found")
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 7,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "404" in (res.error or "")


async def test_publisher_handles_tool_no_data() -> None:
    agent, stub = _agent()
    stub.next_result = ToolResult.ok("github_comment", data=None)
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 7,
            "github_token": "secret",
        },
    )
    res = await agent.execute(ctx)
    assert res.success is False


# ---------------------------------------------------------------------------
# max_issues_per_lens is forwarded to the renderer
# ---------------------------------------------------------------------------


async def test_publisher_respects_max_issues_per_lens() -> None:
    """A non-default cap shrinks the rendered body."""
    agent, stub = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "diff_payload": _diff_payload(),
            "repo": "foo/bar",
            "pr_number": 7,
            "github_token": "secret",
            "max_issues_per_lens": 0,  # summary table only
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    body = stub.captured[0]["body"]
    # With cap=0 there is no per-issue <details> block, just the
    # table. The diff title is still in the body.
    assert "## Website Audit Diff" in body
    # The per-issue title is omitted under the cap.
    assert "Missing meta description" not in body


# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------


def test_publisher_is_registered() -> None:
    from dhrubo.agents.base_agent import agent_registry

    assert "publisher" in agent_registry.roles()
    assert agent_registry.get("publisher") is PublisherAgent
