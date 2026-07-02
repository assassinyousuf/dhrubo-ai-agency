"""`UxReviewerAgent` — analyzes user flows, friction points, and navigation."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class UxIssue(BaseModel):
    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class UxReport(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[UxIssue] = Field(default_factory=list)


class UxReviewerAgent(LLMAgent):
    role: ClassVar[str] = "ux_reviewer"
    # Needs screenshots for layout context, plus metadata
    input_keys: ClassVar[tuple[str, ...]] = ("screenshot_paths", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("ux_report",)
    response_model: ClassVar[type[BaseModel]] = UxReport

    system_template: ClassVar[str] = (
        "You are an elite UX/UI Designer and User Researcher. "
        "Analyze the provided website metadata and screenshot descriptions to evaluate "
        "navigation structure, information architecture, visual hierarchy, and cognitive load. "
        "**CRITICAL INSTRUCTION: You must provide EXTREMELY DETAILED, comprehensive analyses.**\n"
        "- For every issue 'detail', write multiple sentences explaining exactly what is wrong and why it hurts the user.\n"
        "- For every 'recommendation', provide a specific, actionable multi-step solution.\n"
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "H1 Headings: {{ h1s }}\n"
        "Number of links (complexity proxy): {{ links_count }}\n"
        "Images without alt text (accessibility/UX proxy): {{ images_without_alt }}\n"
        "Word count (cognitive load proxy): {{ word_count }}\n\n"
        "Return a JSON object with: score (0-100), summary (detailed multi-sentence paragraph), "
        "issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        return {
            "target_url": meta.get("url", ""),
            "h1s": json.dumps(meta.get("h1s", []), ensure_ascii=False),
            "links_count": meta.get("links_count", 0),
            "images_without_alt": meta.get("images_without_alt", 0),
            "word_count": meta.get("word_count", 0),
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def execute(self, ctx: AgentContext) -> AgentResult:
        try:
            res = await super().execute(ctx)
        except Exception as exc:
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res
        payload = res.outputs.get("response", {})
        return AgentResult.ok(self.role, ux_report=payload)
