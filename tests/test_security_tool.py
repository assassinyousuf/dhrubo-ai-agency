"""Tests for :mod:`dhrubo.tools.security_tool`."""

from __future__ import annotations

import pytest
from dhrubo.tools.security_tool import (
    SECURITY_HEADERS,
    SecurityParams,
    SecurityTool,
)
from dhrubo.tools.tool_interface import ToolContext


def _tool(monkeypatch: pytest.MonkeyPatch) -> SecurityTool:
    """Return a security tool with no real sleeps in retries."""
    tool = SecurityTool()
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )
    return tool


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_runs_security_when_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _ok(**_kwargs):
        return {
            "success": True,
            "error": None,
            "status_code": 200,
            "headers": {
                "content-security-policy": "default-src 'self'",
                "strict-transport-security": "max-age=63072000",
                "server": "ECS (sec/9740)",
                "set-cookie": "session=abc; Path=/",
            },
            "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _ok)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    data = res.data
    assert data["skipped"] is False
    assert data["scheme"] == "https"
    assert data["is_https"] is True
    # Critical checks present.
    severities = [c["severity"] for c in data["checks"]]
    assert "major" in severities  # cookie-insecure
    assert "minor" in severities  # referrer-policy etc.
    assert "info" in severities  # csp-present + server-banner-version
    # Headers lists.
    assert "content-security-policy" in data["headers_seen"]
    assert "strict-transport-security" in data["headers_seen"]
    assert "referrer-policy" in data["headers_missing"]
    # Cookies parsed.
    assert data["cookie_flags"][0]["name"] == "session"
    assert data["cookie_flags"][0]["secure"] is False
    # Server banner version leak flagged.
    assert any(c["id"] == "server-banner-version" for c in data["checks"])


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


async def test_skips_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _fail(**_kwargs):
        return {"success": False, "error": "DNS resolution failed", "headers": {}, "status_code": None}

    monkeypatch.setattr(tool, "_do_call", _fail)
    params = SecurityParams(url="https://nope.invalid/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert "DNS" in res.data["reason"] or "fail" in res.data["reason"].lower()
    assert res.data["headers_seen"] == []
    assert set(res.data["headers_missing"]) == set(SECURITY_HEADERS)


async def test_skips_when_web_fetch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception inside _do_call → graceful skip, not exception."""
    tool = _tool(monkeypatch)

    async def _boom(**_kwargs):
        raise RuntimeError("connect error")

    monkeypatch.setattr(tool, "_do_call", _boom)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert "connect error" in res.data["reason"].lower()


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


async def test_flags_missing_csp_as_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _no_csp(**_kwargs):
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {"server": "nginx"}, "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _no_csp)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert checks_by_id["csp-missing"]["severity"] == "critical"
    assert checks_by_id["csp-missing"]["present"] is False


async def test_flags_https_downgrade_as_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _http(**_kwargs):
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {"content-security-policy": "default-src 'self'"},
            "final_url": "http://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _http)
    params = SecurityParams(url="http://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert checks_by_id["https-downgrade"]["severity"] == "critical"


async def test_flags_insecure_cookies_as_major(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _cookies(**_kwargs):
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {
                "content-security-policy": "default-src 'self'",
                "set-cookie": "session=abc; Path=/",
            },
            "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _cookies)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    cookie_checks = [c for c in res.data["checks"] if c["id"].startswith("cookie-insecure")]
    assert len(cookie_checks) == 1
    assert cookie_checks[0]["severity"] == "major"


async def test_flags_missing_hsts_as_major_on_https(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _no_hsts(**_kwargs):
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {"content-security-policy": "default-src 'self'"},
            "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _no_hsts)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert checks_by_id["hsts-missing"]["severity"] == "major"


# ---------------------------------------------------------------------------
# Headers parsing
# ---------------------------------------------------------------------------


async def test_headers_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed-case header keys are normalised."""
    tool = _tool(monkeypatch)

    async def _mixed(**_kwargs):
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {
                "Content-Security-Policy": "default-src 'self'",
                "Strict-Transport-Security": "max-age=63072000",
                "Server": "ECS",
            },
            "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _mixed)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    assert "content-security-policy" in res.data["headers_seen"]
    assert "strict-transport-security" in res.data["headers_seen"]


# ---------------------------------------------------------------------------
# Param validation + retry + is_available
# ---------------------------------------------------------------------------


async def test_rejects_empty_url(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run({"url": ""}, ToolContext(requester_role="security"))
    assert res.success is False
    assert "Invalid params" in (res.error or "")


async def test_retry_on_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=3, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )

    attempt = {"n": 0}

    async def _flaky(**_kwargs):
        attempt["n"] += 1
        if attempt["n"] < 2:
            raise RuntimeError("transient")
        return {
            "success": True, "error": None, "status_code": 200,
            "headers": {"content-security-policy": "default-src 'self'"},
            "final_url": "https://example.com/",
        }

    monkeypatch.setattr(tool, "_do_call", _flaky)
    params = SecurityParams(url="https://example.com/")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="security"))
    assert res.success is True
    assert res.data["skipped"] is False
    assert attempt["n"] == 2


def test_is_available_returns_true() -> None:
    """httpx is a core dep, so security scanning is always available."""
    assert SecurityTool.is_available() is True
