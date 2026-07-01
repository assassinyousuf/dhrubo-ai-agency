"""Tests for :mod:`dhrubo.agents.accessibility_reviewer`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from dhrubo.agents.accessibility_reviewer import (
    AccessibilityReport,
    AccessibilityReviewerAgent,
)
from dhrubo.agents.base_agent import AgentContext
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.tools.axe_tool import AxeTool, format_violations_for_prompt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str) -> MockProvider:
    p = MockProvider()

    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)

    p.complete = _complete  # type: ignore[assignment]
    return p


def _agent() -> AccessibilityReviewerAgent:
    """Default agent — uses real AxeTool; tests patch is_available/_do_call."""
    return AccessibilityReviewerAgent()


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


async def test_skips_when_no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AxeTool reports unavailable, the agent short-circuits with
    the fully-shaped skip report and the LLM is never called."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: False))
    agent = _agent()
    provider = _make_provider("ignored")
    called = {"n": 0}

    async def _track(request: LLMRequest) -> LLMCompletion:
        called["n"] += 1
        return LLMCompletion(content="ignored", model=request.model)

    provider.complete = _track  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["a11y_report"]
    assert payload["score"] is None
    assert payload["skipped"] is True
    assert "skipped" in payload["summary"].lower()
    assert payload["issues"][0]["severity"] == "info"
    assert payload["violations_count"] == 0
    assert payload["tags_run"] == []
    assert called["n"] == 0  # LLM never called


async def test_skips_when_missing_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``target_url`` (and no ``url`` in page_metadata), the
    agent must still emit a skip report rather than fail."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))
    agent = _agent()
    provider = _make_provider("ignored")
    ctx = AgentContext(
        role=agent.role,
        inputs={"page_metadata": {}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["a11y_report"]
    assert payload["skipped"] is True
    assert payload["score"] is None
    assert payload["issues"][0]["severity"] == "info"


async def test_skips_when_tool_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If AxeTool.safe_run() returns ``success=False``, the agent must
    still degrade to a skip report — never blow up the audit."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))

    async def _fail(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.fail("axe", error="nope")

    monkeypatch.setattr(AxeTool, "safe_run", _fail)

    agent = _agent()
    provider = _make_provider("ignored")
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["a11y_report"]
    assert payload["skipped"] is True
    assert payload["score"] is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_calls_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool returns canned axe payload → LLM returns valid JSON →
    report carries LLM score + issues + back-filled axe metadata."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "axe",
            data={
                "skipped": False,
                "reason": None,
                "url": "https://x/",
                "final_url": "https://x/",
                "viewport": "desktop",
                "tags_run": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
                "violations": [
                    {
                        "id": "image-alt",
                        "impact": "critical",
                        "severity": "critical",
                        "description": "Images must have alt text.",
                        "help": "Images must have alternate text",
                        "help_url": "https://dequeuniversity.com/rules/axe/4.0/image-alt",
                        "tags": ["wcag2a"],
                        "nodes_count": 1,
                        "sample_target": "img.hero",
                        "sample_html": "<img src='hero.png'>",
                    },
                    {
                        "id": "color-contrast",
                        "impact": "serious",
                        "severity": "major",
                        "description": "Contrast ratio must meet WCAG AA.",
                        "help": "Elements must have sufficient color contrast",
                        "help_url": "https://dequeuniversity.com/rules/axe/4.0/color-contrast",
                        "tags": ["wcag2aa"],
                        "nodes_count": 2,
                        "sample_target": "p.lead",
                        "sample_html": "<p class='lead'>x</p>",
                    },
                ],
                "violations_count": 2,
                "passes_count": 1,
                "fetched_at": "2025-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(AxeTool, "safe_run", _ok)

    agent = _agent()
    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 55,
                    "summary": "Critical image-alt and major contrast violations.",
                    "issues": [
                        {
                            "severity": "critical",
                            "title": "Missing alt text",
                            "detail": "Hero image has no alt attribute.",
                            "recommendation": "Provide descriptive alt text.",
                        },
                        {
                            "severity": "major",
                            "title": "Insufficient color contrast",
                            "detail": "Lead paragraph contrast ratio fails WCAG AA.",
                            "recommendation": "Darken text or lighten background.",
                        },
                    ],
                }
            ),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _capture  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["a11y_report"]
    # LLM-driven fields.
    assert payload["score"] == 55
    assert len(payload["issues"]) == 2
    assert payload["issues"][0]["severity"] == "critical"
    # Back-filled from axe payload.
    assert payload["violations_count"] == 2
    assert "wcag2a" in payload["tags_run"]
    assert payload["viewport"] == "desktop"
    assert payload["final_url"] == "https://x/"
    assert payload["fetched_at"] == "2025-01-01T00:00:00+00:00"
    assert payload["skipped"] is False
    # The user prompt must embed violations + JSON summary.
    user_msg = next(m for m in captured["request"].messages if m.role.value == "user")
    assert "image-alt" in user_msg.content
    assert "color-contrast" in user_msg.content
    assert "axe" in user_msg.content.lower() or "wcag" in user_msg.content.lower()


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_retry_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """First LLM call returns garbage; second returns valid JSON."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "axe",
            data={
                "skipped": False,
                "reason": None,
                "url": "https://x/",
                "final_url": "https://x/",
                "viewport": "desktop",
                "tags_run": ["wcag2a"],
                "violations": [],
                "violations_count": 0,
                "passes_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(AxeTool, "safe_run", _ok)

    agent = _agent()
    call_count = {"n": 0}

    async def _flaky(request: LLMRequest) -> LLMCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCompletion(content="{not json", model=request.model)
        return LLMCompletion(
            content=json.dumps({"score": 80, "summary": "ok", "issues": []}),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _flaky  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    assert call_count["n"] == 2


async def test_retry_on_schema_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM returns a score outside [0, 100] → schema validation fails."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "axe",
            data={
                "skipped": False,
                "reason": None,
                "url": "https://x/",
                "final_url": "https://x/",
                "viewport": "desktop",
                "tags_run": ["wcag2a"],
                "violations": [],
                "violations_count": 0,
                "passes_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(AxeTool, "safe_run", _ok)

    agent = _agent()

    async def _bad(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(
            content=json.dumps({"score": 999, "summary": "x", "issues": []}),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _bad  # type: ignore[assignment]
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is False


async def test_missing_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """No LLM configured → the agent fails loudly (never silently skip
    when the data is good)."""
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "axe",
            data={
                "skipped": False,
                "reason": None,
                "url": "https://x/",
                "final_url": "https://x/",
                "viewport": "desktop",
                "tags_run": ["wcag2a"],
                "violations": [],
                "violations_count": 0,
                "passes_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(AxeTool, "safe_run", _ok)

    agent = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {"url": "https://x/"},
        },
        llm=None,
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "no LLM" in (res.error or "")


# ---------------------------------------------------------------------------
# Severity mapping — axe impact 1:1
# ---------------------------------------------------------------------------


def test_severity_mapping_impact_to_severity() -> None:
    """axe impact: critical/serious/moderate/minor → framework severity
    must stay 1:1 via the prompt builder (the LLM confirms)."""
    from dhrubo.tools.axe_tool import _severity_for

    assert _severity_for("critical") == "critical"
    assert _severity_for("serious") == "major"
    assert _severity_for("moderate") == "minor"
    assert _severity_for("minor") == "info"
    assert _severity_for(None) == "info"
    assert _severity_for("Unknown") == "info"


def test_format_violations_for_prompt_severity_in_line() -> None:
    """The prompt helper embeds severity tags verbatim; the LLM pass
    just consumes those labels."""
    violations = [
        {"severity": "critical", "id": "image-alt", "impact": "critical",
         "help": "h", "nodes_count": 1},
        {"severity": "info", "id": "v0", "impact": "minor",
         "help": "h", "nodes_count": 1},
    ]
    text = format_violations_for_prompt(violations)
    assert "[CRITICAL]" in text
    assert "[INFO]" in text
    assert "image-alt" in text


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_accessibility_report_score_optional() -> None:
    r = AccessibilityReport(summary="no score")
    assert r.score is None
    assert r.issues == []
    assert r.violations_count == 0
    assert r.tags_run == []
    assert r.viewport is None
    assert r.skipped is False


def test_accessibility_report_schema_rejects_bad_severity() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AccessibilityReport(
            score=80,
            summary="x",
            issues=[
                {"severity": "fatal", "title": "t", "detail": "d",
                 "recommendation": "r"}
            ],
        )


def test_accessibility_report_schema_rejects_bad_score() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AccessibilityReport(score=150, summary="x")
