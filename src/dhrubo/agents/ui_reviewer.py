"""`UiReviewerAgent` — vision-based UI/UX reviewer.

Reads the three viewport screenshots produced by :class:`ScreenshotAgent`
(desktop, tablet, mobile), feeds them to a multimodal LLM, and emits a
structured UI sub-report (score, summary, issues, viewports_seen).

When no screenshots are available the agent short-circuits with
``score=None`` and a single ``info`` issue, so downstream consumers never
have to special-case "no UI section".

The agent inherits from :class:`LLMAgent` so it reuses:

- Jinja2 prompt rendering
- JSON-mode request + Pydantic validation
- the retry loop on parse / schema failures

The only piece it overrides is :meth:`_call_llm` to attach
:class:`ImageRef` entries to the user message — the parent signature
has no image hook, so we rebuild the :class:`LLMRequest` here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent
from dhrubo.core.errors import AgentError
from dhrubo.core.logger import get_logger
from dhrubo.llm.interface import ImageRef, LLMMessage, LLMRequest

_log = get_logger("agents.ui_reviewer")


class UiIssue(BaseModel):
    """One UI/UX issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class UiReport(BaseModel):
    """Structured UI/UX sub-report."""

    score: int | None = Field(default=None, ge=0, le=100)
    summary: str = ""
    issues: list[UiIssue] = Field(default_factory=list)
    viewports_seen: list[str] = Field(default_factory=list)


# Fully-shaped fallback returned when no screenshots are available, so the
# downstream report writer never has to branch on "was the LLM called?".
_NO_SCREENSHOT_REPORT = UiReport(
    score=None,
    summary="UI review skipped — no screenshots were available for this page.",
    issues=[
        UiIssue(
            severity="info",
            title="UI review not run",
            detail="The screenshot agent did not produce any images for this page.",
            recommendation=(
                "Re-run with a real browser driver (DHRUBO_USE_REAL_BROWSER=1) "
                "or check that the screenshot task completed successfully."
            ),
        )
    ],
    viewports_seen=[],
)


class UiReviewerAgent(LLMAgent):
    role: ClassVar[str] = "ui_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("screenshot_paths", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("ui_report",)
    response_model: ClassVar[type[BaseModel]] = UiReport

    system_template: ClassVar[str] = (
        "You are a senior UI/UX reviewer. You are given the same web page "
        "rendered at three viewports (desktop, tablet, mobile). Produce a "
        "structured visual audit. Focus on: layout balance, typographic "
        "hierarchy, alignment, contrast, spacing, viewport responsiveness, "
        "obvious broken images, and visual regressions.\n"
        "**CRITICAL INSTRUCTION: You must provide EXTREMELY DETAILED, comprehensive analyses.**\n"
        "- For every issue 'detail', write multiple sentences explaining exactly what is visually wrong.\n"
        "- For every 'recommendation', provide a specific, actionable multi-step solution.\n"
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Number of screenshots attached: {{ viewport_count }}\n"
        "Viewports (in order): {{ viewport_names }}\n\n"
        "Return a JSON object with: score (0-100 or null to indicate the "
        "reviewer could not produce a numeric grade), summary (detailed multi-sentence paragraph), "
        "issues (array of {severity, title, detail, recommendation}), and "
        "viewports_seen (array of viewport names you actually reviewed). "
        "Severity values: critical, major, minor, info."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        shots: list[dict[str, Any]] = ctx.inputs.get("screenshot_paths") or []
        return {
            "target_url": meta.get("url", ""),
            "final_url": meta.get("final_url", ""),
            "title": meta.get("title") or "(no title)",
            "viewport_count": len(shots),
            "viewport_names": ", ".join(s.get("viewport", "?") for s in shots) or "(none)",
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def _call_llm(
        self,
        ctx: AgentContext,
        *,
        system: str,
        user: str,
    ) -> str:
        """Attach one :class:`ImageRef` per available screenshot.

        Short-circuits the LLM call (returning a fully-shaped
        :class:`UiReport`) when no screenshots are present.
        """
        if ctx.llm is None:
            raise AgentError(
                f"Agent '{ctx.role}' has no LLM provider configured",
                context={"role": ctx.role},
            )

        shots: list[dict[str, Any]] = ctx.inputs.get("screenshot_paths") or []
        images: list[ImageRef] = []
        for shot in shots:
            path = shot.get("path")
            if not path or not Path(path).exists():
                _log.warning("ui.screenshot.missing", extra={"path": path})
                continue
            images.append(ImageRef(path=path, detail="auto"))

        if not images:
            # No-screenshot fallback: skip the LLM and return a valid
            # UiReport JSON so the report writer can render it uniformly.
            _log.info("ui.skip_no_screenshots", extra={"role": self.role})
            return json.dumps(_NO_SCREENSHOT_REPORT.model_dump())

        # Provider-level metadata hint for routes that need it.
        meta = ctx.metadata if isinstance(ctx.metadata, dict) else {}
        temperature = float(meta.get("temperature", 0.3))
        max_tokens = int(meta.get("max_tokens", 4096))
        timeout_seconds = float(meta.get("timeout_seconds", 120))

        request = LLMRequest(
            model=self._resolve_model(ctx),
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user, images=images),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            response_format_json=self.use_json_mode and self.response_model is not None,
            metadata={"vision": True},
        )
        completion = await ctx.llm.complete(request)
        return str(completion.content)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        try:
            res = await super().execute(ctx)
        except Exception as exc:  # AgentError subclasses already structured
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res
        payload = res.outputs.get("response", {})
        # Back-fill viewports_seen from the actual inputs when the LLM
        # either omitted the field or returned an empty list — both are
        # unhelpful for the report writer.
        shots = ctx.inputs.get("screenshot_paths") or []
        seen = payload.get("viewports_seen")
        if not seen and shots:
            payload["viewports_seen"] = [s.get("viewport", "?") for s in shots]
        return AgentResult.ok(self.role, ui_report=payload)
