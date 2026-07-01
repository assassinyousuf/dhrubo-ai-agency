"""Tests for :mod:`dhrubo.tools.github_comment_tool` (M12)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from dhrubo.tools.github_comment_tool import GitHubCommentParams, GitHubCommentTool
from dhrubo.tools.tool_interface import ToolContext


def _ok_response(json_body: dict[str, Any]) -> httpx.Response:
    req = httpx.Request("POST", "https://api.github.com/repos/foo/bar/issues/1/comments")
    return httpx.Response(200, json=json_body, request=req)


def _err_response(status: int, body: dict[str, Any] | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://api.github.com/repos/foo/bar/issues/1/comments")
    return httpx.Response(
        status,
        json=body or {"message": f"HTTP {status}"},
        request=req,
    )


def _tool(*, max_attempts: int = 1) -> GitHubCommentTool:
    """Return a tool with no config-dir retry lookup and a tight retry policy."""
    tool = GitHubCommentTool()
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=max_attempts,
        initial_delay_seconds=0.001,
        max_delay_seconds=0.01,
        jitter=False,
    )
    return tool


_PARAMS = GitHubCommentParams(
    repo="foo/bar",
    pr_number=1,
    body="# Hello",
    token="secret",
)


# ---------------------------------------------------------------------------
# Endpoint + auth
# ---------------------------------------------------------------------------


async def test_post_pr_comment_builds_url() -> None:
    tool = _tool()
    captured: dict[str, Any] = {}

    async def _capture(*, url, headers, json, timeout_seconds):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _ok_response({"id": 12345, "html_url": "https://github.com/foo/bar/pull/1#issuecomment-12345"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _capture)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success
        assert captured["url"] == "https://api.github.com/repos/foo/bar/issues/1/comments"
        # Default base URL has no trailing slash.
        assert captured["url"].endswith("/issues/1/comments")
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_includes_auth_header() -> None:
    tool = _tool()
    captured: dict[str, Any] = {}

    async def _capture(*, url, headers, json, timeout_seconds):
        captured["headers"] = headers
        captured["json"] = json
        return _ok_response({"id": 1, "html_url": "https://x/"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _capture)
    try:
        await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert captured["headers"]["Authorization"] == "Bearer secret"
        assert captured["headers"]["Accept"] == "application/vnd.github+json"
        assert "X-GitHub-Api-Version" in captured["headers"]
        assert captured["json"] == {"body": "# Hello"}
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_custom_base_url() -> None:
    """A custom ``api_base_url`` (e.g. GitHub Enterprise) is honoured."""
    tool = _tool()
    captured: dict[str, Any] = {}

    async def _capture(*, url, headers, json, timeout_seconds):
        captured["url"] = url
        return _ok_response({"id": 1, "html_url": "https://gh.enterprise/foo/bar/pull/1#issuecomment-1"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _capture)
    try:
        params = GitHubCommentParams(
            repo="foo/bar",
            pr_number=1,
            body="x",
            token="t",
            api_base_url="https://gh.enterprise/api/v3/",
        )
        await tool.safe_run(params.model_dump(), ToolContext(requester_role="publisher"))
        # Trailing slashes on base URL are stripped.
        assert captured["url"] == "https://gh.enterprise/api/v3/repos/foo/bar/issues/1/comments"
    finally:
        monkeypatch_outer.undo()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_post_pr_comment_returns_url() -> None:
    tool = _tool()

    async def _ok(*, url, headers, json, timeout_seconds):
        return _ok_response({
            "id": 42,
            "html_url": "https://github.com/foo/bar/pull/1#issuecomment-42",
        })

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _ok)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success
        assert res.data["comment_url"] == "https://github.com/foo/bar/pull/1#issuecomment-42"
        assert res.data["id"] == 42
        assert res.data["repo"] == "foo/bar"
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_rejects_missing_html_url() -> None:
    """A 200 with no ``html_url`` is treated as a tool failure."""
    tool = _tool()

    async def _weird(*, url, headers, json, timeout_seconds):
        return _ok_response({"id": 1})  # no html_url

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _weird)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is False
        assert "html_url" in (res.error or "")
    finally:
        monkeypatch_outer.undo()


# ---------------------------------------------------------------------------
# Error handling — 4xx vs 5xx
# ---------------------------------------------------------------------------


async def test_post_pr_comment_does_not_retry_on_4xx() -> None:
    """A 4xx from GitHub is a hard failure — no retry."""
    tool = _tool(max_attempts=3)
    call_count = {"n": 0}

    async def _forbidden(*, url, headers, json, timeout_seconds):
        call_count["n"] += 1
        return _err_response(403, {"message": "Resource not accessible by integration"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _forbidden)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is False
        assert call_count["n"] == 1  # NOT retried
        assert "403" in (res.error or "")
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_retries_on_5xx() -> None:
    """A 5xx is retried per policy; failure surfaces after max_attempts."""
    tool = _tool(max_attempts=3)
    call_count = {"n": 0}

    async def _server_err(*, url, headers, json, timeout_seconds):
        call_count["n"] += 1
        return _err_response(500, {"message": "Internal Server Error"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _server_err)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is False
        # retried max_attempts times
        assert call_count["n"] == 3
        assert "500" in (res.error or "")
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_retries_on_5xx_then_succeeds() -> None:
    """Retries cover transient 5xx: after one 5xx the second call succeeds."""
    tool = _tool(max_attempts=3)
    call_count = {"n": 0}

    async def _flaky(*, url, headers, json, timeout_seconds):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _err_response(502, {"message": "Bad Gateway"})
        return _ok_response({"id": 99, "html_url": "https://x/#99"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _flaky)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is True
        assert call_count["n"] == 2
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_retries_on_transport_error() -> None:
    """Network errors (httpx.HTTPError) are retried."""
    tool = _tool(max_attempts=2)
    call_count = {"n": 0}

    async def _flaky(*, url, headers, json, timeout_seconds):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("ECONNRESET")
        return _ok_response({"id": 7, "html_url": "https://x/#7"})

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _flaky)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is True
        assert call_count["n"] == 2
    finally:
        monkeypatch_outer.undo()


async def test_post_pr_comment_propagates_transport_error_after_retries() -> None:
    """If all retries fail on network error, the tool returns a failure."""
    tool = _tool(max_attempts=2)

    async def _always_fail(*, url, headers, json, timeout_seconds):
        raise httpx.ConnectError("ECONNRESET")

    monkeypatch_outer = pytest.MonkeyPatch()
    monkeypatch_outer.setattr(tool, "_do_call", _always_fail)
    try:
        res = await tool.safe_run(_PARAMS.model_dump(), ToolContext(requester_role="publisher"))
        assert res.success is False
        assert "transport error" in (res.error or "")
    finally:
        monkeypatch_outer.undo()


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------


async def test_post_pr_comment_rejects_invalid_params() -> None:
    tool = _tool()
    res = await tool.safe_run(
        {"repo": "no-slash", "pr_number": 1, "body": "x", "token": "t"},
        ToolContext(requester_role="publisher"),
    )
    assert res.success is False
    assert "Invalid params" in (res.error or "")


async def test_post_pr_comment_rejects_pr_zero() -> None:
    tool = _tool()
    res = await tool.safe_run(
        {"repo": "foo/bar", "pr_number": 0, "body": "x", "token": "t"},
        ToolContext(requester_role="publisher"),
    )
    assert res.success is False


# ---------------------------------------------------------------------------
# Capability gate
# ---------------------------------------------------------------------------


def test_is_available() -> None:
    assert GitHubCommentTool.is_available() is True
