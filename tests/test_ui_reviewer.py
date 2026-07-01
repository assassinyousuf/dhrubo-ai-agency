"""Tests for :mod:`dhrubo.agents.ui_reviewer`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.ui_reviewer import UiReport, UiReviewerAgent
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.tools.image_utils import _PNG_1x1


def _make_pngs(tmp_path: Path) -> list[dict[str, Any]]:
    """Create three 1x1 PNG files and return their screenshot descriptors."""
    out: list[dict[str, Any]] = []
    for name, vp in (("desktop", "desktop"), ("mobile", "mobile"), ("tablet", "tablet")):
        p = tmp_path / f"{name}.png"
        p.write_bytes(_PNG_1x1)
        out.append({"path": str(p), "viewport": vp, "width": 1, "height": 1})
    return out


def _make_provider(content: str) -> MockProvider:
    p = MockProvider()
    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)
    p.complete = _complete  # type: ignore[assignment]
    return p


# ---------------------------------------------------------------------------
# No-screenshot fallback
# ---------------------------------------------------------------------------


async def test_skips_when_no_screenshots() -> None:
    agent = UiReviewerAgent()
    # The provider should never be called when there are no screenshots.
    provider = _make_provider("")  # content irrelevant
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": [],
            "page_metadata": {"url": "https://x/", "title": "x"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["ui_report"]
    assert payload["score"] is None
    assert "skipped" in payload["summary"].lower()
    assert payload["issues"][0]["severity"] == "info"
    assert "UI review not run" in payload["issues"][0]["title"]


# ---------------------------------------------------------------------------
# Image attachment
# ---------------------------------------------------------------------------


async def test_attaches_images_to_request(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()

    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        # Return a valid UiReport JSON for the LLM call to succeed.
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 80,
                    "summary": "Looks good.",
                    "issues": [],
                    "viewports_seen": ["desktop", "mobile", "tablet"],
                }
            ),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _capture  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T", "final_url": "https://x/"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    req: LLMRequest = captured["request"]
    user_msgs = [m for m in req.messages if m.role.value == "user"]
    assert len(user_msgs) == 1
    assert len(user_msgs[0].images) == 3
    # All images should point to the local files we wrote.
    for ref in user_msgs[0].images:
        assert ref.path is not None
        assert Path(ref.path).exists()
    assert req.metadata.get("vision") is True
    assert req.response_format_json is True


# ---------------------------------------------------------------------------
# viewports_seen back-fill
# ---------------------------------------------------------------------------


async def test_viewports_seen_back_filled(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()
    # LLM returns valid JSON with viewports_seen: [] — the agent should
    # overwrite the empty list with the actual viewport names from inputs.
    provider = _make_provider(
        json.dumps({"score": 80, "summary": "ok", "issues": [], "viewports_seen": []})
    )
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["ui_report"]
    assert payload["viewports_seen"] == ["desktop", "mobile", "tablet"]


# ---------------------------------------------------------------------------
# Happy path / retries
# ---------------------------------------------------------------------------


async def test_valid_json_response(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()
    provider = _make_provider(
        json.dumps(
            {
                "score": 85,
                "summary": "Strong visual hierarchy.",
                "issues": [
                    {
                        "severity": "minor",
                        "title": "Cramped mobile nav",
                        "detail": "Tap targets below 44px.",
                        "recommendation": "Increase padding.",
                    }
                ],
                "viewports_seen": ["desktop", "mobile", "tablet"],
            }
        )
    )
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    payload = res.outputs["ui_report"]
    assert payload["score"] == 85
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["severity"] == "minor"


async def test_retry_on_invalid_json(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()

    call_count = {"n": 0}

    async def _flaky(request: LLMRequest) -> LLMCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCompletion(content="{not json", model=request.model)
        return LLMCompletion(
            content=json.dumps(
                {
                    "score": 80,
                    "summary": "ok",
                    "issues": [],
                    "viewports_seen": ["desktop", "mobile", "tablet"],
                }
            ),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _flaky  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    assert call_count["n"] == 2


async def test_retry_on_schema_fail(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()

    async def _bad(request: LLMRequest) -> LLMCompletion:
        # Score out of range (Pydantic ge=0, le=100) — schema validation fails.
        return LLMCompletion(
            content=json.dumps({"score": 500, "summary": "ok", "issues": []}),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _bad  # type: ignore[assignment]

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    # Agent surfaces parse/schema failure as AgentResult.fail.
    assert res.success is False
    assert res.error is not None


async def test_missing_llm_when_screenshots_present(tmp_path: Path) -> None:
    shots = _make_pngs(tmp_path)
    agent = UiReviewerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=None,
    )
    res = await agent.execute(ctx)
    assert res.success is False
    assert "no LLM" in (res.error or "")


async def test_missing_path_is_skipped(tmp_path: Path) -> None:
    """Screenshots that point to non-existent files are dropped, not fatal."""
    agent = UiReviewerAgent()
    # One valid, one missing. The agent should still call the LLM with 1 image,
    # not short-circuit to the no-screenshot fallback.
    valid = tmp_path / "valid.png"
    valid.write_bytes(_PNG_1x1)
    shots = [
        {"path": str(valid), "viewport": "desktop", "width": 1, "height": 1},
        {"path": str(tmp_path / "gone.png"), "viewport": "mobile", "width": 1, "height": 1},
    ]
    captured: dict[str, Any] = {}

    async def _capture(request: LLMRequest) -> LLMCompletion:
        captured["request"] = request
        return LLMCompletion(
            content=json.dumps(
                {"score": 70, "summary": "ok", "issues": [], "viewports_seen": ["desktop"]}
            ),
            model=request.model,
        )

    provider = MockProvider()
    provider.complete = _capture  # type: ignore[assignment]
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "screenshot_paths": shots,
            "page_metadata": {"url": "https://x/", "title": "T"},
        },
        llm=provider,
    )
    res = await agent.execute(ctx)
    assert res.success is True
    user_msg = next(m for m in captured["request"].messages if m.role.value == "user")
    assert len(user_msg.images) == 1
    assert user_msg.images[0].path == str(valid)


# ---------------------------------------------------------------------------
# Schema-level sanity
# ---------------------------------------------------------------------------


def test_ui_report_schema_rejects_bad_severity() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UiReport(score=80, summary="x", issues=[{"severity": "fatal", "title": "t", "detail": "d", "recommendation": "r"}])


def test_ui_report_score_optional() -> None:
    r = UiReport(summary="no score")
    assert r.score is None
    assert r.viewports_seen == []
    assert r.issues == []
