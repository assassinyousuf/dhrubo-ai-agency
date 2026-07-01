"""`PerformanceReviewerAgent` — PageSpeed-Insights-backed performance reviewer.

Hybrid shape:

1. Call :class:`LighthouseTool` to get raw PSI data (or a skip payload when
   no API key is configured).
2. **If the tool returned a skip payload, short-circuit**: emit a fully-shaped
   :class:`PerformanceReport` with ``score=None`` and an ``info`` issue,
   never call the LLM. Same UX as the UI reviewer's no-screenshot path.
3. Otherwise render a prompt that contains the trimmed PSI summary and ask
   the LLM to score + turn opportunities into severity-rated ``issues``.

Inherits from :class:`LLMAgent` so it reuses prompt rendering, JSON-mode
request, Pydantic validation, and the retry loop. The only override is
:meth:`execute`, which runs the tool first.
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
from dhrubo.tools.lighthouse_tool import LighthouseParams, LighthouseTool
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.performance_reviewer")


class PerformanceIssue(BaseModel):
    """One performance issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class PerformanceMetric(BaseModel):
    """A core-web-vitals or Lighthouse metric."""

    id: str
    title: str
    value: float | None = None
    display_value: str = ""
    score: float | None = None


class PerformanceOpportunity(BaseModel):
    """An actionable optimization opportunity."""

    id: str
    title: str
    savings_ms: int = 0
    display_savings: str = ""
    score: float | None = None


class PerformanceReport(BaseModel):
    """Structured performance sub-report."""

    score: int | None = Field(default=None, ge=0, le=100)
    summary: str = ""
    issues: list[PerformanceIssue] = Field(default_factory=list)
    metrics: list[PerformanceMetric] = Field(default_factory=list)
    opportunities: list[PerformanceOpportunity] = Field(default_factory=list)
    has_field_data: bool = False
    strategy: str | None = None
    final_url: str | None = None
    fetched_at: str | None = None
    skipped: bool = False


# Fully-shaped fallback returned when PSI can't be called.
_NO_PERF_DATA_REPORT = PerformanceReport(
    score=None,
    summary="Performance review skipped — no PageSpeed API key was configured.",
    issues=[
        PerformanceIssue(
            severity="info",
            title="Performance review not run",
            detail=(
                "The Lighthouse tool did not call PageSpeed Insights because "
                "neither PAGESPEED_API_KEY nor GOOGLE_API_KEY is set."
            ),
            recommendation=(
                "Set PAGESPEED_API_KEY (or GOOGLE_API_KEY) in the environment "
                "and re-run to enable performance auditing."
            ),
        )
    ],
    metrics=[],
    opportunities=[],
    has_field_data=False,
    strategy=None,
    final_url=None,
    fetched_at=None,
    skipped=True,
)


# Cap the size of the PSI snippet embedded in the prompt.
_MAX_RAW_BYTES = 8_000


class PerformanceReviewerAgent(LLMAgent):
    role: ClassVar[str] = "performance_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("performance_report",)
    required_tools: ClassVar[tuple[str, ...]] = ("lighthouse",)
    response_model: ClassVar[type[BaseModel]] = PerformanceReport

    system_template: ClassVar[str] = (
        "You are a senior web-performance reviewer. You are given a trimmed "
        "PageSpeed Insights audit for a single URL, including its overall "
        "performance score, core-web-vitals metrics, top opportunities "
        "(estimated savings in ms), and whether CrUX field data was "
        "available. Produce a structured performance audit. Focus on: "
        "LCP / FCP / TBT / CLS, render-blocking resources, image weight, "
        "third-party scripts, and obvious mobile vs desktop regressions. "
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Strategy: {{ strategy }}\n"
        "Has CrUX field data: {{ has_field_data }}\n"
        "Raw PSI score: {{ psi_score }}\n\n"
        "Metrics (id / display / value):\n{{ metrics_lines }}\n\n"
        "Top opportunities (id / title / savings_ms):\n{{ opportunities_lines }}\n\n"
        "Trimmed raw payload (JSON):\n----\n{{ psi_summary }}\n----\n\n"
        "Return a JSON object with: score (0-100 or null), summary (one "
        "sentence), issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info. The metrics and "
        "opportunities lists should reflect what the PSI data actually "
        "contained; do not invent new metrics."
    )

    def __init__(
        self,
        *,
        lighthouse_tool: LighthouseTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        super().__init__(prompt_dir=None)
        self._tool: LighthouseTool = lighthouse_tool or LighthouseTool(config_dir=config_dir)

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        psi = (ctx.metadata or {}).get("_psi_payload") or {}
        metrics_lines = _format_metrics(psi.get("metrics", []))
        opportunities_lines = _format_opportunities(psi.get("opportunities", []))
        raw = psi.get("raw") or {}
        psi_summary = json.dumps(raw, ensure_ascii=False)[:_MAX_RAW_BYTES]
        return {
            "target_url": meta.get("url", "") or ctx.inputs.get("target_url", ""),
            "final_url": meta.get("final_url", ""),
            "title": meta.get("title") or "(no title)",
            "strategy": psi.get("strategy") or "(unknown)",
            "has_field_data": "yes" if psi.get("has_field_data") else "no",
            "psi_score": psi.get("score") if psi.get("score") is not None else "n/a",
            "metrics_lines": metrics_lines,
            "opportunities_lines": opportunities_lines,
            "psi_summary": psi_summary,
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def _fetch_psi(self, ctx: AgentContext) -> dict[str, Any]:
        """Call the Lighthouse tool and return its data dict (skip-payload or full)."""
        url = ctx.inputs.get("target_url") or (ctx.inputs.get("page_metadata") or {}).get("url")
        if not url:
            # No URL = the agent can't do anything. Surface a skip payload
            # so the report writer can render the placeholder uniformly.
            return {
                "skipped": True,
                "reason": "missing target_url",
                "score": None,
                "strategy": None,
                "final_url": None,
                "fetched_at": None,
                "has_field_data": False,
                "metrics": [],
                "opportunities": [],
                "raw": None,
            }

        params = LighthouseParams(url=str(url), strategy="mobile")
        tool_ctx = ToolContext(requester_role=self.role)
        result = await self._tool.safe_run(
            params.model_dump(),
            tool_ctx,
        )
        if not result.success or result.data is None:
            # PSI failed; surface as skip-payload so the audit still produces
            # a report. The error is logged for the operator.
            _log.warning(
                "performance.tool_failed",
                extra={"role": self.role, "error": result.error, "url": str(url)},
            )
            return {
                "skipped": True,
                "reason": result.error or "lighthouse tool failed",
                "score": None,
                "strategy": "mobile",
                "final_url": None,
                "fetched_at": None,
                "has_field_data": False,
                "metrics": [],
                "opportunities": [],
                "raw": None,
            }
        return dict(result.data or {})

    async def execute(self, ctx: AgentContext) -> AgentResult:
        psi = await self._fetch_psi(ctx)
        # Stash the PSI payload on ctx.metadata so build_variables() can find
        # it. We can't set arbitrary attributes on a slotted dataclass, so
        # we use the documented metadata dict instead.
        if isinstance(ctx.metadata, dict):
            ctx.metadata["_psi_payload"] = psi
        else:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                ctx.metadata = {"_psi_payload": psi}

        if psi.get("skipped"):
            # Skip the LLM entirely; ship the pre-shaped no-data report.
            _log.info("performance.skipped", extra={"role": self.role, "reason": psi.get("reason")})
            return AgentResult.ok(
                self.role,
                performance_report=_NO_PERF_DATA_REPORT.model_dump(),
            )

        try:
            res = await super().execute(ctx)
        except Exception as exc:  # AgentError subclasses already structured
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res

        payload = res.outputs.get("response", {})
        # The LLM only sets score / summary / issues. Pull metrics/opportunities
        # from the PSI payload directly so the report is never empty.
        payload["metrics"] = psi.get("metrics", [])
        payload["opportunities"] = psi.get("opportunities", [])
        payload["has_field_data"] = bool(psi.get("has_field_data"))
        payload["strategy"] = psi.get("strategy")
        payload["final_url"] = psi.get("final_url")
        payload["fetched_at"] = psi.get("fetched_at")
        payload["skipped"] = False
        return AgentResult.ok(self.role, performance_report=payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_metrics(metrics: list[dict[str, Any]]) -> str:
    if not metrics:
        return "(no metrics)"
    lines: list[str] = []
    for m in metrics:
        lines.append(f"- {m.get('id','?')}: {m.get('display_value','')} ({m.get('value')})")
    return "\n".join(lines)


def _format_opportunities(opps: list[dict[str, Any]]) -> str:
    if not opps:
        return "(no opportunities)"
    lines: list[str] = []
    for o in opps[:10]:  # cap at top 10 to keep prompt small
        lines.append(f"- {o.get('id','?')}: {o.get('title','')} ({o.get('savings_ms',0)} ms)")
    return "\n".join(lines)


__all__ = [
    "PerformanceIssue",
    "PerformanceMetric",
    "PerformanceOpportunity",
    "PerformanceReport",
    "PerformanceReviewerAgent",
]
