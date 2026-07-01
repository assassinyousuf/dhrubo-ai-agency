"""`AccessibilityReviewerAgent` — axe-core-backed WCAG/ARIA reviewer.

Hybrid shape (mirrors :class:`PerformanceReviewerAgent`):

1. Call :class:`AxeTool` to run axe-core in a real browser.
2. **If the tool returned a skip payload, short-circuit**: emit a
   fully-shaped :class:`AccessibilityReport` with ``score=None`` and an
   ``info`` issue pointing at the missing ``[a11y]`` extra, never call
   the LLM.
3. Otherwise render a prompt that contains the trimmed axe
   violations and ask the LLM to score + turn the violations into
   severity-rated ``issues[]``.

Inherits from :class:`LLMAgent` so it reuses prompt rendering,
JSON-mode request, Pydantic validation, and the retry loop.

Severity mapping: axe's ``impact`` (critical/serious/moderate/minor)
maps 1:1 to the framework's severity vocabulary
(critical/major/minor/info). The LLM confirms / refines in the
editor pass; the rubric stays consistent across reviewers.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.axe_tool import AxeParams, AxeTool, format_violations_for_prompt
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.accessibility_reviewer")


class AccessibilityIssue(BaseModel):
    """One accessibility issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class AccessibilityReport(BaseModel):
    """Structured accessibility sub-report."""

    score: int | None = Field(default=None, ge=0, le=100)
    summary: str = ""
    issues: list[AccessibilityIssue] = Field(default_factory=list)
    violations_count: int = 0
    tags_run: list[str] = Field(default_factory=list)
    viewport: str | None = None
    final_url: str | None = None
    fetched_at: str | None = None
    skipped: bool = False


# Fully-shaped fallback returned when axe can't be called.
_NO_A11Y_DATA_REPORT = AccessibilityReport(
    score=None,
    summary="Accessibility review skipped — axe-core could not be run.",
    issues=[
        AccessibilityIssue(
            severity="info",
            title="Accessibility review not run",
            detail=(
                "The axe tool did not run because neither the `playwright` "
                "package nor `axe-playwright-python` is installed."
            ),
            recommendation=(
                "Install the `[a11y]` extra (`pip install -e '.[a11y]'` "
                "followed by `playwright install chromium`) and re-run to "
                "enable WCAG 2.0/2.1 auditing."
            ),
        )
    ],
    violations_count=0,
    tags_run=[],
    viewport=None,
    final_url=None,
    fetched_at=None,
    skipped=True,
)


# Cap the size of the axe snippet embedded in the prompt.
_MAX_AXES_SUMMARY_BYTES = 6_000


class AccessibilityReviewerAgent(LLMAgent):
    role: ClassVar[str] = "accessibility_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("a11y_report",)
    required_tools: ClassVar[tuple[str, ...]] = ("axe",)
    response_model: ClassVar[type[BaseModel]] = AccessibilityReport

    system_template: ClassVar[str] = (
        "You are a senior accessibility reviewer specializing in WCAG 2.0 "
        "/ 2.1 compliance. You are given the structured violations from "
        "an axe-core audit for a single URL, including rule id, "
        "description, impact, node counts, and a sample element. "
        "Produce a structured accessibility audit. Focus on: color "
        "contrast, semantic structure, ARIA, keyboard navigation, "
        "form labels, image alt text, language attributes, and "
        "WCAG-essential best practices. Output ONLY a JSON object "
        "matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Viewport: {{ viewport }}\n"
        "WCAG tags run: {{ tags_run }}\n"
        "Total axe violations: {{ violations_count }}\n\n"
        "Top violations (id / severity / impact / help / nodes):\n"
        "{{ violations_lines }}\n\n"
        "Trimmed axe payload (JSON):\n----\n{{ axes_summary }}\n----\n\n"
        "Return a JSON object with: score (0-100 or null), summary "
        "(one sentence), issues (array of {severity, title, detail, "
        "recommendation}). Severity values: critical, major, minor, "
        "info. Map axe's impact 1:1 (critical→critical, serious→major, "
        "moderate→minor, minor→info). The issues list should reflect "
        "what the axe data actually contained; do not invent new "
        "rules."
    )

    def __init__(
        self,
        *,
        axe_tool: AxeTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        super().__init__(prompt_dir=None)
        self._tool: AxeTool = axe_tool or AxeTool(config_dir=config_dir)

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        axe = (ctx.metadata or {}).get("_a11y_payload") or {}
        violations = axe.get("violations", []) or []
        violations_lines = format_violations_for_prompt(violations)
        # Summarise raw axe JSON for context, capped.
        raw_blob = {
            "url": axe.get("url"),
            "viewport": axe.get("viewport"),
            "tags_run": axe.get("tags_run", []),
            "violations": violations,
        }
        axes_summary = json.dumps(raw_blob, ensure_ascii=False)[:_MAX_AXES_SUMMARY_BYTES]
        return {
            "target_url": meta.get("url", "") or ctx.inputs.get("target_url", ""),
            "final_url": meta.get("final_url", "") or axe.get("final_url", ""),
            "title": meta.get("title") or "(no title)",
            "viewport": axe.get("viewport") or "(unknown)",
            "tags_run": ", ".join(axe.get("tags_run") or []) or "(none)",
            "violations_count": axe.get("violations_count", 0),
            "violations_lines": violations_lines,
            "axes_summary": axes_summary,
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def _fetch_axe(self, ctx: AgentContext) -> dict[str, Any]:
        """Call the axe tool and return its data dict (skip-payload or full)."""
        url = ctx.inputs.get("target_url") or (ctx.inputs.get("page_metadata") or {}).get("url")
        if not url:
            return {
                "skipped": True,
                "reason": "missing target_url",
                "url": None,
                "final_url": None,
                "viewport": None,
                "tags_run": [],
                "violations": [],
                "violations_count": 0,
                "passes_count": 0,
                "fetched_at": None,
            }

        params = AxeParams(url=str(url), viewport="desktop")
        tool_ctx = ToolContext(requester_role=self.role)
        result = await self._tool.safe_run(params.model_dump(), tool_ctx)
        if not result.success or result.data is None:
            _log.warning(
                "accessibility.tool_failed",
                extra={"role": self.role, "error": result.error, "url": str(url)},
            )
            return {
                "skipped": True,
                "reason": result.error or "axe tool failed",
                "url": str(url),
                "final_url": None,
                "viewport": "desktop",
                "tags_run": [],
                "violations": [],
                "violations_count": 0,
                "passes_count": 0,
                "fetched_at": None,
            }
        return dict(result.data or {})

    async def execute(self, ctx: AgentContext) -> AgentResult:
        axe = await self._fetch_axe(ctx)
        # Stash the axe payload on ctx.metadata so build_variables() can
        # find it. We can't set arbitrary attributes on a slotted
        # dataclass, so we use the documented metadata dict instead.
        if isinstance(ctx.metadata, dict):
            ctx.metadata["_a11y_payload"] = axe
        else:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                ctx.metadata = {"_a11y_payload": axe}

        if axe.get("skipped"):
            _log.info(
                "accessibility.skipped",
                extra={"role": self.role, "reason": axe.get("reason")},
            )
            return AgentResult.ok(
                self.role,
                a11y_report=_NO_A11Y_DATA_REPORT.model_dump(),
            )

        try:
            res = await super().execute(ctx)
        except Exception as exc:  # AgentError subclasses already structured
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res

        payload = res.outputs.get("response", {})
        # The LLM only sets score/summary/issues. Back-fill the
        # canonical axe counters from the payload so the report is
        # never empty.
        payload["violations_count"] = axe.get("violations_count", 0)
        payload["tags_run"] = list(axe.get("tags_run") or [])
        payload["viewport"] = axe.get("viewport")
        payload["final_url"] = axe.get("final_url") or axe.get("url")
        payload["fetched_at"] = axe.get("fetched_at")
        payload["skipped"] = False
        return AgentResult.ok(self.role, a11y_report=payload)


__all__ = [
    "AccessibilityIssue",
    "AccessibilityReport",
    "AccessibilityReviewerAgent",
]
