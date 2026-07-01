"""`GitHubCommentTool` — post Markdown comments to a GitHub PR.

Used by M12 to turn a ``diff.json`` (produced by ``run-audit
--diff-since`` or ``dhrubo diff --json``) into actionable PR
feedback.

Design notes:

- **Thin wrapper around httpx.** No third-party SDK — the API
  surface is one ``POST /repos/{owner}/{repo}/issues/{n}/comments``
  with an ``Authorization: Bearer <token>`` header. We re-use the
  same ``httpx.AsyncClient`` + ``retry_async`` pattern as
  :mod:`dhrubo.tools.lighthouse_tool` and
  :mod:`dhrubo.tools.web_fetch_tool`.
- **5xx + network errors are retried; 4xx are not.** 4xx means
  the request is bad in a way that retrying won't fix (bad
  token, missing PR, malformed body). We raise a
  :class:`ToolError` immediately and let the publisher agent
  surface the failure.
- **Retry policy** is loaded from
  :mod:`dhrubo.config.loader` (the ``github_post`` entry in
  ``config/retry_policies.yaml``), falling back to
  :data:`dhrubo.core.retry.DEFAULT_RETRY`.
- **Token never logged.** The ``Authorization`` header is set
  in-memory only; the response is read for the comment URL +
  id and that's the only data we return.

The tool is gated on :func:`is_available` checking that
``httpx`` is importable (always true in our env).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from dhrubo.config.loader import load_retry_policies
from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.core.retry import DEFAULT_RETRY, RetryConfig, retry_async
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.github_comment")

# The default base URL — overridable via params (e.g. tests point
# this at a local mock server, or a GitHub Enterprise install
# points it at a custom origin).
_DEFAULT_API_BASE_URL = "https://api.github.com"


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``github_post`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("github_comment.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("github_post", DEFAULT_RETRY)


class GitHubCommentParams(BaseModel):
    """Inputs for :class:`GitHubCommentTool`."""

    repo: str = Field(
        min_length=3,
        max_length=200,
        pattern=r"^[^/\s]+/[^/\s]+$",
        description="GitHub repo (owner/name), e.g. 'octocat/Hello-World'.",
    )
    pr_number: int = Field(ge=1, le=10_000_000, description="Pull request number to comment on.")
    body: str = Field(
        min_length=1,
        max_length=65_536,
        description="Markdown body of the comment (GitHub's hard cap is 65,536 chars).",
    )
    token: str = Field(
        min_length=1,
        max_length=2_000,
        description="GitHub PAT or installation token with `repo` scope.",
    )
    api_base_url: str = Field(
        default=_DEFAULT_API_BASE_URL,
        min_length=1,
        max_length=2_000,
        description="GitHub API base URL (override for GitHub Enterprise).",
    )
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)


class GitHubCommentTool(Tool[GitHubCommentParams]):
    """Post a Markdown comment on a GitHub PR via the REST API."""

    name: ClassVar[str] = "github_comment"
    description: ClassVar[str] = (
        "Post a Markdown comment on a GitHub PR issue. Uses "
        "`POST /repos/{owner}/{repo}/issues/{n}/comments` over httpx "
        "with bearer-token auth and an exponential-backoff retry policy."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("repo", "string", description="GitHub repo (owner/name)."),
        ToolParameter("pr_number", "int", description="PR number to comment on (>= 1)."),
        ToolParameter("body", "string", description="Markdown body of the comment."),
        ToolParameter("token", "string", description="GitHub PAT or installation token."),
        ToolParameter("api_base_url", "string", required=False, description="API base URL."),
        ToolParameter("timeout_seconds", "float", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = GitHubCommentParams

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._retry_policy: RetryConfig = _resolve_retry_policy(config_dir)

    # ------------------------------------------------------------------
    # Capability gate
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return True if the tool's required deps are importable.

        httpx is a core dep, so this is always True in our env.
        Kept as a hook so future backends (e.g. aiohttp) can be
        swapped in without rippling changes to the agent.
        """
        try:
            import httpx  # noqa: F401

            return True
        except ImportError:  # pragma: no cover - httpx is core
            return False

    # ------------------------------------------------------------------
    # Hook for tests
    # ------------------------------------------------------------------

    async def _do_call(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json: dict[str, str],
        timeout_seconds: float,
    ) -> httpx.Response:
        """Make a single POST. Override or monkeypatch in tests."""
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            return await client.post(url, headers=headers, json=json)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _endpoint(self, params: GitHubCommentParams) -> str:
        """Build the POST URL, normalising trailing slashes on the base."""
        base = params.api_base_url.rstrip("/")
        return f"{base}/repos/{params.repo}/issues/{params.pr_number}/comments"

    async def run(self, params: GitHubCommentParams, ctx: ToolContext) -> ToolResult:
        url = self._endpoint(params)
        # Never log the token.
        headers = {
            "Authorization": f"Bearer {params.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DhruboAudit/0.12 (+https://example.local)",
        }
        json_body = {"body": params.body}

        _log.info(
            "tool.github_comment.start",
            extra={
                "tool": self.name,
                "repo": params.repo,
                "pr_number": params.pr_number,
                "body_chars": len(params.body),
                "requester": ctx.requester_role,
            },
        )

        try:
            response = await retry_async(
                lambda: _do_call_with_5xx_as_error(
                    tool=self,
                    url=url,
                    headers=headers,
                    json=json_body,
                    timeout_seconds=params.timeout_seconds,
                ),
                policy=self._retry_policy,
                op_name="github_comment.post",
                retriable=(httpx.HTTPError,),
            )
        except httpx.HTTPStatusError as exc:
            # Retries exhausted on a 5xx, OR a non-retriable status.
            # We re-raise 5xx as a ToolError; 4xx is caught separately
            # below by inspecting the response code path. _do_call_5xx
            # raises HTTPStatusError for 5xx; we re-classify it here.
            if exc.response.status_code >= 500:
                err_text = _safe_error_text(exc.response)
                raise ToolError(
                    f"GitHub API returned HTTP {exc.response.status_code} after "
                    f"retries: {err_text}",
                    context={
                        "tool": self.name,
                        "repo": params.repo,
                        "pr_number": params.pr_number,
                        "status_code": exc.response.status_code,
                        "response_excerpt": err_text[:512],
                    },
                ) from exc
            # 4xx raised via HTTPStatusError — treat as the same
            # hard-fail path the 4xx branch would.
            err_text = _safe_error_text(exc.response)
            raise ToolError(
                f"GitHub API rejected the comment with HTTP "
                f"{exc.response.status_code}: {err_text}",
                context={
                    "tool": self.name,
                    "repo": params.repo,
                    "pr_number": params.pr_number,
                    "status_code": exc.response.status_code,
                    "response_excerpt": err_text[:512],
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"github_comment transport error: {exc!r}",
                context={
                    "tool": self.name,
                    "repo": params.repo,
                    "pr_number": params.pr_number,
                },
                cause=exc,
            ) from exc

        # 4xx — bad request; do not retry, surface upstream.
        if 400 <= response.status_code < 500:
            err_text = _safe_error_text(response)
            raise ToolError(
                f"GitHub API rejected the comment with HTTP "
                f"{response.status_code}: {err_text}",
                context={
                    "tool": self.name,
                    "repo": params.repo,
                    "pr_number": params.pr_number,
                    "status_code": response.status_code,
                    "response_excerpt": err_text[:512],
                },
            )

        # 2xx — success. Parse and return the comment URL + id.
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - GitHub always returns JSON
            raise ToolError(
                "GitHub API returned a non-JSON success body",
                context={"tool": self.name, "status": response.status_code},
                cause=exc,
            ) from exc

        comment_url = str(payload.get("html_url", ""))
        comment_id = payload.get("id")
        if not comment_url or comment_id is None:
            raise ToolError(
                "GitHub API success body missing html_url / id",
                context={"tool": self.name, "payload_keys": sorted(payload)},
            )

        return ToolResult.ok(
            self.name,
            data={"comment_url": comment_url, "id": comment_id, "repo": params.repo},
            repo=params.repo,
            pr_number=params.pr_number,
            comment_url=comment_url,
        )


async def _do_call_with_5xx_as_error(
    *,
    tool: GitHubCommentTool,
    url: str,
    headers: dict[str, str],
    json: dict[str, str],
    timeout_seconds: float,
) -> httpx.Response:
    """Wrap :meth:`GitHubCommentTool._do_call` so 5xx surfaces as
    :class:`httpx.HTTPStatusError` (and is therefore eligible for
    the ``retry_async`` policy). 4xx still passes through as a
    non-error response; the outer :meth:`run` short-circuits those.
    """
    response = await tool._do_call(
        url=url,
        headers=headers,
        json=json,
        timeout_seconds=timeout_seconds,
    )
    if response.status_code >= 500:
        # raise_for_status() requires an explicit request on the response.
        request = response.request
        raise httpx.HTTPStatusError(
            f"server error {response.status_code}",
            request=request,
            response=response,
        )
    return response


def _safe_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of a short error snippet from a response body.

    Truncated to 512 chars to avoid log spam.
    """
    try:
        # GitHub error bodies are JSON: {"message": "...", "errors": [...], "documentation_url": "..."}
        payload: Any = response.json()
        if isinstance(payload, dict) and "message" in payload:
            msg = str(payload.get("message", ""))
            errs = payload.get("errors")
            if errs:
                return f"{msg} ({errs})"
            return msg
        if isinstance(payload, dict):
            return str(payload)[:512]
        return str(payload)[:512]
    except Exception:
        return (response.text or "")[:512]


__all__ = [
    "GitHubCommentParams",
    "GitHubCommentTool",
]
