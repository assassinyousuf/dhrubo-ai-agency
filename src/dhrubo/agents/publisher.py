"""`PublisherAgent` — post an audit diff as a GitHub PR comment (M12).

Reads a ``diff_payload`` from ``ctx.inputs`` (preferred) or loads
it from a ``diff_path`` on disk, renders the Markdown body via
:mod:`dhrubo.tools.markdown_diff_renderer`, then posts the
comment via :class:`dhrubo.tools.github_comment_tool.GitHubCommentTool`.

The agent is **deterministic** — no LLM call, no DAG scheduling.
The CLI calls it directly as a thin publisher primitive (just
like ``dhrubo diff`` is a thin history-query primitive).

Inputs (from ``ctx.inputs``):

- ``diff_payload`` *(preferred)*: the structured diff dict
  produced by :class:`dhrubo.tools.diff_tool.DiffTool`.
- ``diff_path`` *(fallback)*: a filesystem path to a
  ``diff.json`` on disk. Loaded if ``diff_payload`` is absent.
- ``repo``: GitHub ``owner/name`` (e.g. ``"octocat/Hello-World"``).
- ``pr_number``: int (>= 1) PR number to comment on.
- ``github_token``: the PAT or installation token. The CLI
  resolves this from the ``GITHUB_TOKEN`` env var.
- ``max_issues_per_lens`` *(optional)*: int cap on per-lens
  issue lists in the rendered Markdown. Defaults to 50.

Outputs:

- ``comment_url``: HTML URL of the posted comment.
- ``comment_id``: numeric GitHub comment id.

Failure modes (return ``success=False`` rather than raising):

- ``diff_payload`` and ``diff_path`` both missing/empty.
- ``diff_path`` set but the file doesn't exist or isn't valid
  JSON.
- ``repo`` / ``pr_number`` / ``github_token`` missing.
- The underlying ``GitHubCommentTool`` reports a failure (4xx
  from GitHub, network error after retries, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.github_comment_tool import GitHubCommentParams, GitHubCommentTool
from dhrubo.tools.markdown_diff_renderer import render_diff_comment
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.publisher")

_DEFAULT_MAX_ISSUES_PER_LENS = 50


class PublisherAgent(BaseAgent):
    """Render a diff payload as Markdown and post it to a GitHub PR."""

    role: ClassVar[str] = "publisher"
    input_keys: ClassVar[tuple[str, ...]] = (
        "diff_payload",
        "diff_path",
        "repo",
        "pr_number",
        "github_token",
        "max_issues_per_lens",
    )
    output_keys: ClassVar[tuple[str, ...]] = ("comment_url", "comment_id")
    required_tools: ClassVar[tuple[str, ...]] = ("github_comment",)

    def __init__(
        self,
        *,
        github_comment_tool: GitHubCommentTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._github_comment_tool = github_comment_tool or GitHubCommentTool(
            config_dir=config_dir
        )
        self._config_dir = config_dir

    async def execute(self, ctx: AgentContext) -> AgentResult:
        # ---- 1. Resolve the diff payload ---------------------------------
        diff_payload = ctx.inputs.get("diff_payload")
        diff_path_raw = ctx.inputs.get("diff_path")

        if not diff_payload and diff_path_raw is not None:
            diff_payload = _load_diff_from_path(diff_path_raw)

        if not diff_payload:
            return AgentResult.fail(
                self.role,
                error=(
                    "no diff to publish: provide 'diff_payload' or 'diff_path' "
                    "in the agent inputs"
                ),
            )

        # ---- 2. Resolve GitHub targeting + auth --------------------------
        repo = ctx.inputs.get("repo")
        pr_number_raw = ctx.inputs.get("pr_number")
        token = ctx.inputs.get("github_token")

        if not repo or not isinstance(repo, str):
            return AgentResult.fail(
                self.role,
                error="'repo' (GitHub owner/name) is required",
            )
        if not token or not isinstance(token, str):
            return AgentResult.fail(
                self.role,
                error=(
                    "'github_token' is required (resolve from the GITHUB_TOKEN env var)"
                ),
            )
        try:
            pr_number = int(pr_number_raw) if pr_number_raw is not None else 0
        except (TypeError, ValueError):
            return AgentResult.fail(
                self.role,
                error=f"'pr_number' must be an integer, got {pr_number_raw!r}",
            )
        if pr_number < 1:
            return AgentResult.fail(
                self.role,
                error=f"'pr_number' must be >= 1, got {pr_number}",
            )

        # ---- 3. Render Markdown body -------------------------------------
        max_issues_per_lens = _coerce_max_issues(
            ctx.inputs.get("max_issues_per_lens")
        )
        body = render_diff_comment(
            diff_payload,
            max_issues_per_lens=max_issues_per_lens,
        )

        # ---- 4. Post via the GitHub tool --------------------------------
        params = GitHubCommentParams(
            repo=repo,
            pr_number=pr_number,
            body=body,
            token=token,
        )
        tool_ctx = ToolContext(requester_role=self.role)
        res = await self._github_comment_tool.safe_run(
            {
                "repo": params.repo,
                "pr_number": params.pr_number,
                "body": params.body,
                "token": params.token,
            },
            tool_ctx,
        )

        if not res.success or not res.data:
            reason = res.error or "github_comment tool returned no data"
            _log.warning(
                "publisher.post_failed",
                extra={"role": self.role, "repo": repo, "pr_number": pr_number, "reason": reason},
            )
            return AgentResult.fail(
                self.role,
                error=reason,
                repo=repo,
                pr_number=pr_number,
            )

        comment_url = str(res.data.get("comment_url", ""))
        comment_id = res.data.get("id")
        if not comment_url:
            # Defensive: the tool's success path always returns a url.
            return AgentResult.fail(
                self.role,
                error="github_comment succeeded but returned no comment_url",
                repo=repo,
                pr_number=pr_number,
            )

        _log.info(
            "publisher.posted",
            extra={
                "role": self.role,
                "repo": repo,
                "pr_number": pr_number,
                "comment_url": comment_url,
            },
        )
        return AgentResult.ok(
            self.role,
            comment_url=comment_url,
            comment_id=comment_id,
            repo=repo,
            pr_number=pr_number,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_diff_from_path(raw: Any) -> dict[str, Any] | None:
    """Read a diff.json from disk. Returns ``None`` on any failure
    (missing file, bad JSON, empty payload)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        path = Path(raw)
    elif isinstance(raw, Path):
        path = raw
    else:
        return None
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _coerce_max_issues(raw: Any) -> int:
    """Coerce ``max_issues_per_lens`` to a non-negative int. Defaults to 50."""
    if raw is None:
        return _DEFAULT_MAX_ISSUES_PER_LENS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ISSUES_PER_LENS
    if value < 0:
        return 0
    return value


__all__ = ["PublisherAgent"]
