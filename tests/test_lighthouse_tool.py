"""Tests for :mod:`dhrubo.tools.lighthouse_tool`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from dhrubo.tools.lighthouse_tool import LighthouseParams, LighthouseTool
from dhrubo.tools.tool_interface import ToolContext

# A minimal but valid PSI v5 payload that exercises the summarizer.
_PSI_PAYLOAD = {
    "id": "https://example.com/",
    "loadingExperience": {
        "id": "https://example.com/",
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2400, "category": "AVERAGE"},
        },
    },
    "lighthouseResult": {
        "finalUrl": "https://example.com/",
        "fetchTime": "2026-07-01T15:00:00Z",
        "userAgent": "Mozilla/5.0 test",
        "categories": {
            "performance": {"score": 0.83, "title": "Performance"},
        },
        "audits": {
            "first-contentful-paint": {
                "id": "first-contentful-paint",
                "title": "First Contentful Paint",
                "numericValue": 1200.0,
                "displayValue": "1.2 s",
                "score": 0.9,
            },
            "largest-contentful-paint": {
                "id": "largest-contentful-paint",
                "title": "Largest Contentful Paint",
                "numericValue": 2400.0,
                "displayValue": "2.4 s",
                "score": 0.7,
            },
            "total-blocking-time": {
                "id": "total-blocking-time",
                "title": "Total Blocking Time",
                "numericValue": 150.0,
                "displayValue": "150 ms",
                "score": 0.95,
            },
            "cumulative-layout-shift": {
                "id": "cumulative-layout-shift",
                "title": "Cumulative Layout Shift",
                "numericValue": 0.05,
                "displayValue": "0.05",
                "score": 0.9,
            },
            "render-blocking-resources": {
                "id": "render-blocking-resources",
                "title": "Eliminate render-blocking resources",
                "numericValue": 600.0,
                "displayValue": "Potential savings of 600 ms",
                "score": 0.5,
                "details": {"type": "opportunity", "overallSavingsMs": 600},
            },
            "unused-css-rules": {
                "id": "unused-css-rules",
                "title": "Reduce unused CSS",
                "numericValue": 200.0,
                "displayValue": "Potential savings of 200 ms",
                "score": 0.8,
                "details": {"type": "opportunity", "overallSavingsMs": 200},
            },
        },
    },
}


def _ok_response(payload: dict[str, Any]) -> httpx.Response:
    req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
    return httpx.Response(200, json=payload, request=req)


def _err_response(status: int) -> httpx.Response:
    req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
    return httpx.Response(status, json={"error": {"message": f"HTTP {status}"}}, request=req)


def _tool(monkeypatch) -> LighthouseTool:
    """Return a tool with no config-dir retry lookup and a known short policy."""
    tool = LighthouseTool()
    # Tighten the retry policy so tests don't wait.
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )
    return tool


# ---------------------------------------------------------------------------
# Skip / fallback path
# ---------------------------------------------------------------------------


async def test_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    tool = _tool(monkeypatch)
    called = {"n": 0}

    async def _blow(*_args, **_kwargs):
        called["n"] += 1
        return _ok_response({})

    monkeypatch.setattr(tool, "_do_call", _blow)
    params = LighthouseParams(url="https://x/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="performance_reviewer"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert res.data["score"] is None
    assert called["n"] == 0  # never made an HTTP call


async def test_rejects_bad_params(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run(
        {"url": ""}, ToolContext(requester_role="performance_reviewer")
    )
    assert res.success is False
    assert "Invalid params" in (res.error or "")


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_successful_call_parses_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = _tool(monkeypatch)

    async def _ok(*_args, **_kwargs):
        return _ok_response(_PSI_PAYLOAD)

    monkeypatch.setattr(tool, "_do_call", _ok)

    params = LighthouseParams(url="https://example.com/")
    res = await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert res.success is True
    data = res.data
    assert data["skipped"] is False
    assert data["score"] == 83  # 0.83 * 100, rounded
    assert data["strategy"] == "mobile"
    assert data["final_url"] == "https://example.com/"
    assert data["has_field_data"] is True
    metric_ids = {m["id"] for m in data["metrics"]}
    assert {
        "first-contentful-paint",
        "largest-contentful-paint",
        "total-blocking-time",
        "cumulative-layout-shift",
    } <= metric_ids
    # Opportunities sorted by savings desc.
    assert data["opportunities"][0]["id"] == "render-blocking-resources"
    assert data["opportunities"][0]["savings_ms"] == 600


async def test_strategy_param_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = _tool(monkeypatch)
    captured: dict[str, Any] = {}

    async def _capture(*, url, params, timeout_seconds):
        captured["url"] = url
        captured["params"] = params
        return _ok_response(_PSI_PAYLOAD)

    monkeypatch.setattr(tool, "_do_call", _capture)

    params = LighthouseParams(url="https://example.com/", strategy="desktop")
    await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert captured["params"]["strategy"] == "desktop"
    assert captured["params"]["key"] == "test-key"


async def test_google_api_key_also_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    tool = _tool(monkeypatch)
    captured: dict[str, Any] = {}

    async def _capture(*, url, params, timeout_seconds):
        captured["params"] = params
        return _ok_response(_PSI_PAYLOAD)

    monkeypatch.setattr(tool, "_do_call", _capture)
    params = LighthouseParams(url="https://example.com/")
    await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert captured["params"]["key"] == "google-key"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_handles_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = _tool(monkeypatch)

    async def _forbidden(*_args, **_kwargs):
        return _err_response(403)

    monkeypatch.setattr(tool, "_do_call", _forbidden)

    params = LighthouseParams(url="https://example.com/")
    res = await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert res.success is False
    assert "403" in (res.error or "")


async def test_handles_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = _tool(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("test connect error")

    monkeypatch.setattr(tool, "_do_call", _boom)

    params = LighthouseParams(url="https://example.com/")
    res = await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert res.success is False
    assert "transport" in (res.error or "").lower()


async def test_handles_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAGESPEED_API_KEY", "test-key")
    tool = _tool(monkeypatch)

    async def _html(*_args, **_kwargs):
        req = httpx.Request("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
        return httpx.Response(200, content=b"<html>not json</html>", request=req)

    monkeypatch.setattr(tool, "_do_call", _html)

    params = LighthouseParams(url="https://example.com/")
    res = await tool.safe_run(
        params.model_dump(), ToolContext(requester_role="performance_reviewer")
    )
    assert res.success is False
    assert "non-json" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# has_api_key helper
# ---------------------------------------------------------------------------


def test_has_api_key_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert LighthouseTool.has_api_key() is False
    monkeypatch.setenv("PAGESPEED_API_KEY", "x")
    assert LighthouseTool.has_api_key() is True
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "y")
    assert LighthouseTool.has_api_key() is True
