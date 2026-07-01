"""Tests for :mod:`dhrubo.agents.security_reviewer`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.security_reviewer import (
    SecurityReport,
    SecurityReviewerAgent,
)
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.tools.security_tool import SecurityTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str) -> MockProvider:
    p = MockProvider()

    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)

    p.complete = _complete  # type: ignore[assignment]
    return p


def _agent() -> SecurityReviewerAgent:
    return SecurityReviewerAgent()


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


async def test_skips_when_no_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    provider = _make_provider("ignored")
    called = {"n": 0}

    async def _track(request: LLMRequest) -> LLMCompletion:
        called["n"] += 1
        return LLMCompletion(content="ignored", model=request.model)

    provider.complete = _track  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={"page_metadata": {}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["security_report"]
    assert payload["score"] is None
    assert payload["skipped"] is True
    assert payload["issues"][0]["severity"] == "info"
    assert called["n"] == 0


async def test_skips_when_tool_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If SecurityTool.safe_run() returns ``success=False``, the agent
    must still degrade to a skip report — never blow up the audit."""
    async def _fail(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.fail("security", error="nope")

    monkeypatch.setattr(SecurityTool, "safe_run", _fail)

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
    payload = res.outputs["security_report"]
    assert payload["skipped"] is True
    assert payload["score"] is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_calls_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecurityTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "security",
            data={
                "skipped": False,
                "reason": None,
                "url": "https://x/",
                "final_url": "https://x/",
                "status_code": 200,
                "scheme": "https",
                "is_https": True,
                "headers_seen": ["content-security-policy", "strict-transport-security"],
                "headers_missing": ["referrer-policy", "x-frame-options"],
                "cookie_flags": [],
                "server_banner": "ECS",
                "checks": [
                    {
                        "id": "csp-present",
                        "severity": "info",
                        "present": True,
                        "value": "default-src 'self'",
                        "finding": "CSP set.",
                        "recommendation": "review directives.",
                    },
                    {
                        "id": "referrer-policy-missing",
                        "severity": "minor",
                        "present": False,
                        "value": None,
                        "finding": "No Referrer-Policy.",
                        "recommendation": "Add Referrer-Policy header.",
                    },
                ],
                "checks_count": 2,
                "fetched_at": "2025-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(SecurityTool, "safe_run", _ok)

    agent = _agent()
    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 70,
                    "summary": "HTTPS + CSP + HSTS present; Referrer-Policy missing.",
                    "issues": [
                        {
                            "severity": "minor",
                            "title": "Referrer-Policy missing",
                            "detail": "No Referrer-Policy header.",
                            "recommendation": "Add Referrer-Policy: strict-origin-when-cross-origin.",
                        }
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
    payload = res.outputs["security_report"]
    # LLM-driven fields.
    assert payload["score"] == 70
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["severity"] == "minor"
    # Back-filled from security payload.
    assert payload["checks_count"] == 2
    assert "content-security-policy" in payload["headers_seen"]
    assert "referrer-policy" in payload["headers_missing"]
    assert payload["scheme"] == "https"
    assert payload["is_https"] is True
    assert payload["final_url"] == "https://x/"
    assert payload["fetched_at"] == "2025-01-01T00:00:00+00:00"
    assert payload["skipped"] is False
    # The user prompt embeds the checks.
    user_msg = next(m for m in captured["request"].messages if m.role.value == "user")
    assert "csp-present" in user_msg.content or "Referrer-Policy" in user_msg.content


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_retry_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecurityTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "security",
            data={
                "skipped": False, "reason": None, "url": "https://x/",
                "final_url": "https://x/", "status_code": 200, "scheme": "https",
                "is_https": True, "headers_seen": [], "headers_missing": [],
                "cookie_flags": [], "server_banner": None,
                "checks": [], "checks_count": 0, "fetched_at": None,
            },
        )

    monkeypatch.setattr(SecurityTool, "safe_run", _ok)
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
        inputs={"target_url": "https://x/", "page_metadata": {"url": "https://x/"}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    assert call_count["n"] == 2


async def test_retry_on_schema_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecurityTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "security",
            data={
                "skipped": False, "reason": None, "url": "https://x/",
                "final_url": "https://x/", "status_code": 200, "scheme": "https",
                "is_https": True, "headers_seen": [], "headers_missing": [],
                "cookie_flags": [], "server_banner": None,
                "checks": [], "checks_count": 0, "fetched_at": None,
            },
        )

    monkeypatch.setattr(SecurityTool, "safe_run", _ok)
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
        inputs={"target_url": "https://x/", "page_metadata": {"url": "https://x/"}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is False


async def test_missing_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecurityTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "security",
            data={
                "skipped": False, "reason": None, "url": "https://x/",
                "final_url": "https://x/", "status_code": 200, "scheme": "https",
                "is_https": True, "headers_seen": [], "headers_missing": [],
                "cookie_flags": [], "server_banner": None,
                "checks": [], "checks_count": 0, "fetched_at": None,
            },
        )

    monkeypatch.setattr(SecurityTool, "safe_run", _ok)
    agent = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={"target_url": "https://x/", "page_metadata": {"url": "https://x/"}},
        llm=None,
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "no LLM" in (res.error or "")


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_security_report_score_optional() -> None:
    r = SecurityReport(summary="no score")
    assert r.score is None
    assert r.issues == []
    assert r.headers_seen == []
    assert r.headers_missing == []
    assert r.scheme is None
    assert r.is_https is None
    assert r.skipped is False


def test_security_report_schema_rejects_bad_severity() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SecurityReport(
            score=80,
            summary="x",
            issues=[{"severity": "fatal", "title": "t", "detail": "d", "recommendation": "r"}],
        )
