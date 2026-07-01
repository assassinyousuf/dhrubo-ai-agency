"""Tests for :mod:`dhrubo.tools.axe_tool`."""

from __future__ import annotations

import pytest
from dhrubo.tools.axe_tool import (
    AxeParams,
    AxeTool,
    _severity_for,
    format_violations_for_prompt,
    normalize_results,
)
from dhrubo.tools.tool_interface import ToolContext


def _tool(monkeypatch: pytest.MonkeyPatch) -> AxeTool:
    """Return an axe tool with no real sleeps in retries."""
    tool = AxeTool()
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )
    return tool


# ---------------------------------------------------------------------------
# Skip path
# ---------------------------------------------------------------------------


async def test_skips_when_playwright_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: False))
    tool = _tool(monkeypatch)
    called = {"n": 0}

    async def _blow(*_args, **_kwargs):
        called["n"] += 1
        return {}

    monkeypatch.setattr(tool, "_do_call", _blow)
    params = AxeParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="accessibility_reviewer"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert "axe-playwright-python" in res.data["reason"].lower() or "playwright" in res.data["reason"].lower()
    assert called["n"] == 0  # _do_call must never run when unavailable


async def test_skips_when_only_axe_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when Playwright is patched-available but `axe-playwright-python`
    isn't importable, the tool must skip rather than fail."""
    # is_available() returns True in this test (we monkey-patch it).
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    async def _blow(*_args, **_kwargs):
        raise RuntimeError("axe lib hidden by test")

    monkeypatch.setattr(tool, "_do_call", _blow)

    params = AxeParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="accessibility_reviewer"))
    # The runtime catches the exception and degrades to a skip payload —
    # the audit never fails because of a missing optional dep.
    assert res.success is True
    assert res.data["skipped"] is True
    assert "axe lib hidden by test" in res.data["reason"].lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_runs_axe_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    raw_axe_payload = {
        "url": "https://example.com/",
        "violations": [
            {
                "id": "color-contrast",
                "impact": "serious",
                "description": "Ensures the contrast between foreground and background colors meets WCAG 2 AA contrast ratio thresholds.",
                "help": "Elements must have sufficient color contrast",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.0/color-contrast",
                "tags": ["wcag2aa", "wcag143"],
                "nodes": [
                    {
                        "target": ["body > p"],
                        "html": "<p style=\"color: #aaa\">low-contrast</p>",
                    }
                ],
            },
            {
                "id": "image-alt",
                "impact": "critical",
                "description": "Ensures <img> elements have alternate text or a role of none or presentation.",
                "help": "Images must have alternate text",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.0/image-alt",
                "tags": ["wcag2a", "wcag111"],
                "nodes": [
                    {"target": ["img.hero"], "html": "<img src='hero.png'>"},
                    {"target": ["img.banner"], "html": "<img src='banner.png'>"},
                ],
            },
        ],
        "passes": [{"id": "document-title"}, {"id": "html-has-lang"}],
    }

    async def _run(**_kwargs):
        return raw_axe_payload

    monkeypatch.setattr(tool, "_do_call", _run)
    params = AxeParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="accessibility_reviewer"))
    assert res.success is True
    data = res.data
    assert data["skipped"] is False
    assert data["violations_count"] == 2
    assert data["passes_count"] == 2
    # Critical impact sorts first.
    severities = [v["severity"] for v in data["violations"]]
    assert severities == ["critical", "major"]
    # Sample element extracted.
    first = data["violations"][0]
    assert first["id"] == "image-alt"
    assert first["nodes_count"] == 2
    assert first["sample_target"] == "img.hero"
    assert "<img" in first["sample_html"]


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_severity_for_impact_mapping() -> None:
    assert _severity_for("critical") == "critical"
    assert _severity_for("serious") == "major"
    assert _severity_for("moderate") == "minor"
    assert _severity_for("minor") == "info"
    assert _severity_for(None) == "info"
    assert _severity_for("Unknown") == "info"


def test_normalize_results_empty() -> None:
    out = normalize_results({})
    assert out["violations"] == []
    assert out["violations_count"] == 0
    assert out["passes_count"] == 0


def test_normalize_results_drops_per_node_html() -> None:
    raw = {
        "url": "https://x/",
        "violations": [
            {
                "id": "v1",
                "impact": "serious",
                "description": "d",
                "help": "h",
                "helpUrl": "u",
                "tags": ["wcag2aa"],
                "nodes": [
                    {"target": ["a"], "html": "<a>" * 50},
                    {"target": ["b"], "html": "<b>" * 50},
                ],
            }
        ],
        "passes": [],
    }
    out = normalize_results(raw)
    assert out["violations"][0]["nodes_count"] == 2
    # No node HTML leaked into the normalized shape.
    assert "nodes" not in out["violations"][0]


def test_format_violations_for_prompt_caps_items() -> None:
    violations = [
        {"severity": "info", "id": f"v{i}", "impact": "minor", "help": "h", "nodes_count": 1}
        for i in range(30)
    ]
    text = format_violations_for_prompt(violations, max_items=5)
    assert "[INFO]" in text
    assert "v0" in text and "v4" in text
    assert "v5" not in text
    assert "more (truncated)" in text


def test_format_violations_for_prompt_empty() -> None:
    assert format_violations_for_prompt([]) == "(no axe violations)"


# ---------------------------------------------------------------------------
# Param validation + retry + transport error
# ---------------------------------------------------------------------------


async def test_rejects_empty_url(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run(
        {"url": ""}, ToolContext(requester_role="accessibility_reviewer")
    )
    assert res.success is False
    assert "Invalid params" in (res.error or "")


async def test_handles_navigation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("navigation failed")

    monkeypatch.setattr(tool, "_do_call", _boom)
    params = AxeParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="accessibility_reviewer"))
    assert res.success is True  # safe_run returns success=True with skipped data
    assert res.data["skipped"] is True
    assert "navigation failed" in res.data["reason"].lower()


async def test_retry_on_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AxeTool, "is_available", staticmethod(lambda: True))
    tool = _tool(monkeypatch)
    # Allow multiple attempts.
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=3, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )

    attempt = {"n": 0}

    async def _flaky(**_kwargs):
        attempt["n"] += 1
        if attempt["n"] < 2:
            raise RuntimeError("transient")
        return {"url": "https://x/", "violations": [], "passes": []}

    monkeypatch.setattr(tool, "_do_call", _flaky)
    params = AxeParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="accessibility_reviewer"))
    assert res.success is True
    assert res.data["skipped"] is False
    assert attempt["n"] == 2


# ---------------------------------------------------------------------------
# is_available helper
# ---------------------------------------------------------------------------


def test_is_available_returns_bool() -> None:
    """The helper returns a bool without raising; import errors are
    swallowed and surface as False (so the tool can degrade gracefully)."""
    assert isinstance(AxeTool.is_available(), bool)
