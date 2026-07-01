"""Tests for :mod:`dhrubo.agents.branding_reviewer`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.branding_reviewer import (
    BrandingReport,
    BrandingReviewerAgent,
)
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.tools.branding_tool import BrandingTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str) -> MockProvider:
    p = MockProvider()

    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)

    p.complete = _complete  # type: ignore[assignment]
    return p


def _agent() -> BrandingReviewerAgent:
    return BrandingReviewerAgent()


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
    payload = res.outputs["branding_report"]
    assert payload["score"] is None
    assert payload["skipped"] is True
    assert payload["issues"][0]["severity"] == "info"
    assert called["n"] == 0


async def test_skips_when_tool_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If BrandingTool.safe_run() returns ``success=False``, the agent
    must still degrade to a skip report — never blow up the audit."""

    async def _fail(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.fail("branding", error="nope")

    monkeypatch.setattr(BrandingTool, "safe_run", _fail)

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
    payload = res.outputs["branding_report"]
    assert payload["skipped"] is True
    assert payload["score"] is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_calls_llm_when_data_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BrandingTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "branding",
            data={
                "skipped": False,
                "url": "https://x/",
                "final_url": "https://x/",
                "logo_url": "https://x/logo.png",
                "favicons": [
                    {"href": "/f.ico", "sizes": "32x32", "type": "image/x-icon", "rel": "icon"}
                ],
                "og_image": "https://x/logo.png",
                "twitter_image": None,
                "theme_color": "#0a0a0a",
                "brand_colors": ["#0a0a0a", "#ffffff"],
                "social_links": [
                    {"platform": "twitter", "url": "https://twitter.com/acme"},
                    {"platform": "github", "url": "https://github.com/acme"},
                ],
                "title_variants": {"page": "Acme", "og": "Acme", "twitter": None},
                "checks": [
                    {
                        "id": "no-theme-color",
                        "severity": "minor",
                        "present": False,
                        "value": None,
                        "finding": "No theme-color meta tag.",
                        "recommendation": "Add theme-color meta.",
                    },
                    {
                        "id": "brand-colors-detected",
                        "severity": "info",
                        "present": True,
                        "value": "#0a0a0a",
                        "finding": "Brand colors found in inline CSS.",
                        "recommendation": "Verify palette usage.",
                    },
                ],
                "checks_count": 2,
                "fetched_at": "2025-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(BrandingTool, "safe_run", _ok)

    agent = _agent()
    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 80,
                    "summary": "Strong logo + OG + theme color; social presence solid.",
                    "issues": [
                        {
                            "severity": "minor",
                            "title": "Theme color missing",
                            "detail": "No theme-color meta on the page.",
                            "recommendation": "Add `<meta name=\"theme-color\">`.",
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
            "page_metadata": {"url": "https://x/", "title": "Acme"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["branding_report"]
    # LLM-driven fields.
    assert payload["score"] == 80
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["severity"] == "minor"
    # Back-filled from branding payload.
    assert payload["logo_url"] == "https://x/logo.png"
    assert payload["og_image"] == "https://x/logo.png"
    assert payload["theme_color"] == "#0a0a0a"
    assert "#0a0a0a" in payload["brand_colors"]
    assert len(payload["social_links"]) == 2
    assert payload["social_links"][0]["platform"] == "twitter"
    assert payload["title_variants"]["page"] == "Acme"
    assert payload["checks_count"] == 2
    assert payload["final_url"] == "https://x/"
    assert payload["fetched_at"] == "2025-01-01T00:00:00+00:00"
    assert payload["skipped"] is False
    # The user prompt embeds the checks.
    user_msg = next(m for m in captured["request"].messages if m.role.value == "user")
    assert "no-theme-color" in user_msg.content or "theme color" in user_msg.content.lower()


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_retry_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BrandingTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "branding",
            data={
                "skipped": False,
                "url": "https://x/",
                "final_url": "https://x/",
                "logo_url": None,
                "favicons": [],
                "og_image": None,
                "twitter_image": None,
                "theme_color": None,
                "brand_colors": [],
                "social_links": [],
                "title_variants": {"page": None, "og": None, "twitter": None},
                "checks": [],
                "checks_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(BrandingTool, "safe_run", _ok)
    agent = _agent()
    call_count = {"n": 0}

    async def _flaky(request: LLMRequest) -> LLMCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCompletion(content="{not json", model=request.model)
        return LLMCompletion(
            content=json.dumps({"score": 70, "summary": "ok", "issues": []}),
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
    monkeypatch.setattr(BrandingTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "branding",
            data={
                "skipped": False,
                "url": "https://x/",
                "final_url": "https://x/",
                "logo_url": None,
                "favicons": [],
                "og_image": None,
                "twitter_image": None,
                "theme_color": None,
                "brand_colors": [],
                "social_links": [],
                "title_variants": {"page": None, "og": None, "twitter": None},
                "checks": [],
                "checks_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(BrandingTool, "safe_run", _ok)
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
    monkeypatch.setattr(BrandingTool, "is_available", staticmethod(lambda: True))

    async def _ok(*_args, **_kwargs):
        from dhrubo.tools.tool_interface import ToolResult

        return ToolResult.ok(
            "branding",
            data={
                "skipped": False,
                "url": "https://x/",
                "final_url": "https://x/",
                "logo_url": None,
                "favicons": [],
                "og_image": None,
                "twitter_image": None,
                "theme_color": None,
                "brand_colors": [],
                "social_links": [],
                "title_variants": {"page": None, "og": None, "twitter": None},
                "checks": [],
                "checks_count": 0,
                "fetched_at": None,
            },
        )

    monkeypatch.setattr(BrandingTool, "safe_run", _ok)
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


def test_branding_report_score_optional() -> None:
    r = BrandingReport(summary="no score")
    assert r.score is None
    assert r.issues == []
    assert r.brand_colors == []
    assert r.social_links == []
    assert r.title_variants == {}
    assert r.logo_url is None
    assert r.theme_color is None
    assert r.skipped is False


def test_branding_report_schema_rejects_bad_severity() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BrandingReport(
            score=80,
            summary="x",
            issues=[{"severity": "fatal", "title": "t", "detail": "d", "recommendation": "r"}],
        )
