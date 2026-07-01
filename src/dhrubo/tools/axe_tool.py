"""`AxeTool` — runs axe-core accessibility audits on a URL.

axe-core is the de-facto accessibility engine. We use the
``axe-playwright-python`` package which ships a Playwright ``Page``
wrapper and a vendored ``axe.min.js``; calling ``Axe().run(page)``
returns a JSON object of ``violations`` and ``passes``.

Design notes:

- **Optional deps**: both ``playwright`` and ``axe-playwright-python``
  live in the ``[a11y]`` extra in ``pyproject.toml``. When either is
  missing, the tool returns a skip-payload (no exception), the audit
  emits a graceful ``n/a (Accessibility review skipped)`` placeholder
  in the markdown report. Mirrors the M5 no-PSI-key / M6
  no-WeasyPrint fallback patterns.
- **Browser driver**: this tool constructs :class:`PlaywrightDriver`
  directly when needed (same pattern as :class:`ScreenshotTool`). The
  abstract :class:`BrowserDriver` interface doesn't expose a page
  handle; axe-core needs one, so we use the concrete driver.
- **Retry policy**: the ``axe_scan`` entry in
  ``config/retry_policies.yaml`` (3 attempts, 1.0s → 10s, jittered).
- **Test seam**: ``_do_call`` is an instance method that callers can
  monkey-patch in tests. Production code drives the PlaywrightDriver
  directly.

Severity mapping (``impact`` → M-framework severity) is **enforced
here** (1:1). The LLM editor pass can still refine per-violation
wording in the audit report, but the rubric stays consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, Field

from dhrubo.config.loader import load_retry_policies
from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.core.retry import DEFAULT_RETRY, RetryConfig, retry_async
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.axe")

# Default WCAG 2.0 / 2.1 levels A + AA — the most common compliance
# targets. Operators can override via the ``run_tags`` param.
_DEFAULT_TAGS: tuple[str, ...] = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa")

# 1:1 impact → severity mapping. Stays the same across all reviewers.
_IMPACT_TO_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "serious": "major",
    "moderate": "minor",
    "minor": "info",
}

# Cap on how many violations the prompt is allowed to inline. axe-core
# has 100+ rules; even wcag2a/aa typically surfaces <30. The cap
# protects the LLM token budget.
_MAX_PROMPT_VIOLATIONS = 25

# A pre-shaped payload returned when Playwright or axe is missing. The
# agent recognises this and short-circuits its LLM call.
_SKIP_PAYLOAD: dict[str, Any] = {
    "skipped": True,
    "reason": "axe-playwright-python + playwright not installed; run `pip install -e '.[a11y]'`",
    "url": None,
    "final_url": None,
    "viewport": None,
    "tags_run": [],
    "violations": [],
    "violations_count": 0,
    "passes_count": 0,
    "fetched_at": None,
}


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``axe_scan`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("axe.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("axe_scan", DEFAULT_RETRY)


class AxeParams(BaseModel):
    """Inputs for :class:`AxeTool`."""

    url: str = Field(min_length=1, max_length=2048)
    viewport: Literal["desktop", "mobile"] = "desktop"
    run_tags: list[str] = Field(default_factory=lambda: list(_DEFAULT_TAGS))
    disable_rules: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=45.0, gt=0.0, le=180.0)


class AxeTool(Tool[AxeParams]):
    """Run axe-core on a URL and return a structured violations summary."""

    name: ClassVar[str] = "axe"
    description: ClassVar[str] = (
        "Run an axe-core accessibility audit on a URL in a real browser and "
        "return a list of violations with impact, node counts, and help links."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Absolute URL to audit."),
        ToolParameter("viewport", "desktop|mobile", required=False),
        ToolParameter("run_tags", "string[]", required=False),
        ToolParameter("disable_rules", "string[]", required=False),
        ToolParameter("timeout_seconds", "float", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = AxeParams

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._retry_policy: RetryConfig = _resolve_retry_policy(config_dir)

    async def run(self, params: AxeParams, ctx: ToolContext) -> ToolResult:
        """Execute the axe scan. See :func:`_run` for the body."""
        return await _run(self, params, ctx)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return True if ``playwright`` and ``axe-playwright-python`` are importable."""
        try:
            import axe_playwright_python  # type: ignore[import-not-found]  # noqa: F401
            import playwright  # noqa: F401
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------
    # Hooks for tests / future enhancements
    # ------------------------------------------------------------------

    async def _do_call(
        self,
        *,
        url: str,
        viewport: str,
        run_tags: list[str],
        disable_rules: list[str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Open Playwright, navigate, run axe, return the raw axe JSON.

        Tests monkey-patch this seam so they never have to spin up a
        real browser. Production code uses :class:`PlaywrightDriver`
        directly.
        """
        from axe_playwright_python.sync_playwright import Axe  # type: ignore[import-not-found]

        from dhrubo.tools.browser_driver import Viewport
        from dhrubo.tools.playwright_impl import PlaywrightDriver

        vp = Viewport.mobile() if viewport == "mobile" else Viewport.desktop()
        driver = PlaywrightDriver(headless=True)
        await driver.start()
        try:
            await driver.navigate(url, timeout_seconds=timeout_seconds)
            ctx = driver._require_context()
            page = await ctx.new_page()
            try:
                await page.set_viewport_size({"width": vp.width, "height": vp.height})
                # Axe's sync wrapper hides async.
                axe = Axe()
                options: dict[str, Any] = {"runOnly": {"type": "tag", "values": run_tags}}
                if disable_rules:
                    options["rules"] = {rule_id: {"enabled": False} for rule_id in disable_rules}
                results = axe.run(page, options=options)
                # axe-playwright-python's `results` is a sync-playwright wrapper
                # that exposes a `.response` / `.violations` etc. Normalize.
                return _extract_results(results)
            finally:
                await page.close()
        finally:
            await driver.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_results(results: Any) -> dict[str, Any]:
    """Tolerantly pull a JSON-like dict out of axe-playwright-python's
    various return shapes (sync wrapper, raw dict, etc.)."""
    # `Axe.run()` from axe-playwright-python returns an object with a
    # `.response` attribute that is the raw axe-core JSON.
    if hasattr(results, "response"):
        raw: Any = results.response
    elif isinstance(results, dict):
        raw = results
    else:
        # Best-effort: try to coerce.
        try:
            raw = dict(results)
        except Exception as exc:  # pragma: no cover - defensive
            raise ToolError(
                f"axe returned unexpected shape: {type(results).__name__}",
                context={"result_type": type(results).__name__},
                cause=exc,
            ) from exc
    return cast(dict[str, Any], raw)


def _severity_for(impact: str | None) -> str:
    """Map an axe impact string to the framework's severity scale."""
    if not impact:
        return "info"
    return _IMPACT_TO_SEVERITY.get(impact.lower(), "info")


def _normalize_violation(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten an axe violation into the agent's compact shape.

    Drops per-node HTML/selector detail (huge), keeps the count + a
    one-line sample so the LLM has enough context. The full JSON
    remains available in the agent's metadata for debugging.
    """
    impact = raw.get("impact") or "minor"
    severity = _severity_for(impact)
    nodes = raw.get("nodes") or []
    nodes_count = len(nodes)
    sample_target = ""
    sample_html = ""
    if nodes:
        first = nodes[0] or {}
        targets = first.get("target") or []
        if targets:
            sample_target = str(targets[0])
        sample_html = (first.get("html") or "")[:200]
    return {
        "id": raw.get("id", ""),
        "impact": impact,
        "severity": severity,
        "description": (raw.get("description") or "").strip(),
        "help": (raw.get("help") or "").strip(),
        "help_url": raw.get("helpUrl", ""),
        "tags": list(raw.get("tags") or []),
        "nodes_count": nodes_count,
        "sample_target": sample_target,
        "sample_html": sample_html,
    }


def normalize_results(raw: dict[str, Any]) -> dict[str, Any]:
    """Top-level normalizer: violations sorted by severity then impact."""
    violations = [_normalize_violation(v) for v in (raw.get("violations") or [])]
    # Sort: critical → major → minor → info, then by id for stability.
    sev_rank = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    violations.sort(key=lambda v: (sev_rank.get(v.get("severity", "info"), 99), v.get("id", "")))
    passes = raw.get("passes") or []
    return {
        "violations": violations,
        "violations_count": len(violations),
        "passes_count": len(passes) if isinstance(passes, list) else 0,
        "url": raw.get("url"),
    }


def format_violations_for_prompt(
    violations: list[dict[str, Any]],
    *,
    max_items: int = _MAX_PROMPT_VIOLATIONS,
) -> str:
    """Render a small bullet list of violations for the LLM prompt."""
    if not violations:
        return "(no axe violations)"
    lines: list[str] = []
    for v in violations[:max_items]:
        lines.append(
            f"- [{v.get('severity', 'info').upper()}] {v.get('id', '?')}: "
            f"{v.get('help', '')} (impact: {v.get('impact', '?')}, "
            f"nodes: {v.get('nodes_count', 0)})"
        )
    if len(violations) > max_items:
        lines.append(f"- … and {len(violations) - max_items} more (truncated)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool runtime
# ---------------------------------------------------------------------------


async def _run(
    tool: AxeTool,
    params: AxeParams,
    ctx: ToolContext,
) -> ToolResult:
    """Shared body — kept module-level so tests can call it directly.

    The first parameter is the owning ``AxeTool`` instance (or any
    object exposing ``_retry_policy``); ``AxeTool.run`` is a thin
    method that delegates here.
    """
    if not AxeTool.is_available():
        _log.info(
            "axe.skipped_unavailable",
            extra={
                "tool": "axe",
                "url": params.url,
                "requester": ctx.requester_role,
            },
        )
        return ToolResult.ok(
            "axe",
            data=dict(_SKIP_PAYLOAD),
            skipped=True,
            url=params.url,
        )

    captured: dict[str, Any] = {}

    async def _attempt() -> dict[str, Any]:
        return await tool._do_call(
            url=params.url,
            viewport=params.viewport,
            run_tags=list(params.run_tags),
            disable_rules=list(params.disable_rules),
            timeout_seconds=params.timeout_seconds,
        )

    try:
        raw = await retry_async(
            _attempt,
            policy=tool._retry_policy,
            op_name="axe.run",
            retriable=(Exception,),
        )
        captured["raw"] = raw
    except Exception as exc:
        _log.warning(
            "axe.run_failed",
            extra={
                "tool": "axe",
                "url": params.url,
                "requester": ctx.requester_role,
                "error": str(exc),
            },
        )
        return ToolResult.ok(
            "axe",
            data=dict(_SKIP_PAYLOAD, reason=f"axe run failed: {exc!r}"),
            skipped=True,
            url=params.url,
        )

    normalized = normalize_results(raw)
    from datetime import UTC, datetime

    return ToolResult.ok(
        "axe",
        data={
            "skipped": False,
            "reason": None,
            "url": params.url,
            "final_url": normalized.get("url"),
            "viewport": params.viewport,
            "tags_run": list(params.run_tags),
            "violations": normalized["violations"],
            "violations_count": normalized["violations_count"],
            "passes_count": normalized["passes_count"],
            "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "raw_violations_count": len(raw.get("violations") or []),
        },
        url=params.url,
        violations_count=normalized["violations_count"],
    )


__all__ = [
    "AxeParams",
    "AxeTool",
    "format_violations_for_prompt",
    "normalize_results",
]
