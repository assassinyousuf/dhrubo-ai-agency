"""Tests for :mod:`dhrubo.agents.performance_reviewer`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.performance_reviewer import (
    PerformanceReport,
    PerformanceReviewerAgent,
)
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.tools.lighthouse_tool import LighthouseTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str) -> MockProvider:
    p = MockProvider()
    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)
    p.complete = _complete  # type: ignore[assignment]
    return p


def _agent(*, monkeypatch) -> PerformanceReviewerAgent:
    """Return an agent with no API key (so PSI returns the skip payload)."""
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    return PerformanceReviewerAgent()


# ---------------------------------------------------------------------------
# Skip path
# ---------------------------------------------------------------------------


async def test_skips_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(monkeypatch=monkeypatch)
    provider = _make_provider("ignored")
    called = {"n": 0}

    async def _track(request: LLMRequest) -> LLMCompletion:
        called["n"] += 1
        return LLMCompletion(content="ignored", model=request.model)

    provider.complete = _track  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={"target_url": "https://x/", "page_metadata": {"url": "https://x/", "title": "T"}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["performance_report"]
    assert payload["score"] is None
    assert payload["skipped"] is True
    assert "skipped" in payload["summary"].lower()
    assert payload["issues"][0]["severity"] == "info"
    assert called["n"] == 0  # LLM never called


async def test_skips_when_missing_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(monkeypatch=monkeypatch)
    provider = _make_provider("ignored")
    ctx = AgentContext(
        role=agent.role,
        inputs={"page_metadata": {}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["performance_report"]
    assert payload["skipped"] is True
    assert "missing" in payload["summary"].lower() or payload["issues"][0]["severity"] == "info"


# ---------------------------------------------------------------------------
# Happy path (tool returns data, LLM returns valid JSON)
# ---------------------------------------------------------------------------


async def test_calls_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")

    # Pre-build an agent and inject a Lighthouse tool whose _do_call is mocked.
    tool = LighthouseTool()
    from dhrubo.config.models import RetryConfig
    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )

    psi_payload = {
        "id": "https://x/",
        "loadingExperience": {},
        "lighthouseResult": {
            "finalUrl": "https://x/",
            "categories": {"performance": {"score": 0.5}},
            "audits": {
                "largest-contentful-paint": {
                    "id": "largest-contentful-paint",
                    "title": "LCP",
                    "numericValue": 4000.0,
                    "displayValue": "4.0 s",
                    "score": 0.3,
                },
                "render-blocking-resources": {
                    "id": "render-blocking-resources",
                    "title": "Eliminate render-blocking resources",
                    "numericValue": 1000.0,
                    "displayValue": "Potential savings of 1,000 ms",
                    "score": 0.2,
                    "details": {"type": "opportunity", "overallSavingsMs": 1000},
                },
            },
        },
    }

    import httpx

    async def _ok(*_args, **_kwargs):
        req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
        return httpx.Response(200, json=psi_payload, request=req)

    monkeypatch.setattr(tool, "_do_call", _ok)

    agent = PerformanceReviewerAgent(lighthouse_tool=tool)

    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 50,
                    "summary": "Slow LCP; render-blocking JS hurts mobile.",
                    "issues": [
                        {
                            "severity": "major",
                            "title": "Slow LCP",
                            "detail": "LCP 4.0s on mobile.",
                            "recommendation": "Defer non-critical JS.",
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
        inputs={"target_url": "https://x/", "page_metadata": {"url": "https://x/", "title": "T"}},
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["performance_report"]
    assert payload["score"] == 50
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["severity"] == "major"
    # Metrics and opportunities should be back-filled from the PSI payload.
    assert any(m["id"] == "largest-contentful-paint" for m in payload["metrics"])
    assert any(o["id"] == "render-blocking-resources" for o in payload["opportunities"])
    # The user prompt should embed the trimmed raw payload.
    user_msg = next(m for m in captured["request"].messages if m.role.value == "user")
    # Either the user template references "psi" / "performance", or the
    # metrics / opportunities lines embed the audit IDs.
    assert (
        "psi" in user_msg.content.lower()
        or "largest-contentful-paint" in user_msg.content
    )
    assert "render-blocking-resources" in user_msg.content


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_retry_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = LighthouseTool()
    import httpx
    async def _ok(*_args, **_kwargs):
        req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
        return httpx.Response(
            200,
            json={"id": "https://x/", "lighthouseResult": {"categories": {"performance": {"score": 0.5}}, "audits": {}}},
            request=req,
        )
    monkeypatch.setattr(tool, "_do_call", _ok)

    agent = PerformanceReviewerAgent(lighthouse_tool=tool)
    call_count = {"n": 0}

    async def _flaky(request: LLMRequest) -> LLMCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCompletion(content="{not json", model=request.model)
        return LLMCompletion(
            content=json.dumps({"score": 50, "summary": "ok", "issues": []}),
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
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = LighthouseTool()
    import httpx
    async def _ok(*_args, **_kwargs):
        req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
        return httpx.Response(
            200,
            json={"id": "https://x/", "lighthouseResult": {"categories": {"performance": {"score": 0.5}}, "audits": {}}},
            request=req,
        )
    monkeypatch.setattr(tool, "_do_call", _ok)

    agent = PerformanceReviewerAgent(lighthouse_tool=tool)

    async def _bad(request: LLMRequest) -> LLMCompletion:
        # score out of range -> schema validation fails
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
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = LighthouseTool()
    import httpx
    async def _ok(*_args, **_kwargs):
        req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
        return httpx.Response(
            200,
            json={"id": "https://x/", "lighthouseResult": {"categories": {"performance": {"score": 0.5}}, "audits": {}}},
            request=req,
        )
    monkeypatch.setattr(tool, "_do_call", _ok)
    agent = PerformanceReviewerAgent(lighthouse_tool=tool)
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


def test_performance_report_score_optional() -> None:
    r = PerformanceReport(summary="no score")
    assert r.score is None
    assert r.metrics == []
    assert r.opportunities == []
    assert r.skipped is False


def test_performance_report_schema_rejects_bad_severity() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PerformanceReport(
            score=80,
            summary="x",
            issues=[{"severity": "fatal", "title": "t", "detail": "d", "recommendation": "r"}],
        )
