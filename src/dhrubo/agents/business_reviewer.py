"""`BusinessReviewerAgent` — analyzes business positioning and value prop."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class BusinessIssue(BaseModel):
    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class BusinessReport(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[BusinessIssue] = Field(default_factory=list)


class BusinessReviewerAgent(LLMAgent):
    role: ClassVar[str] = "business_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("page_metadata", "dom_html")
    output_keys: ClassVar[tuple[str, ...]] = ("business_report",)
    response_model: ClassVar[type[BaseModel]] = BusinessReport

    system_template: ClassVar[str] = (
        "You are an elite Business Strategist and Copywriter. "
        "Analyze the provided website data to evaluate the company's value proposition, "
        "brand positioning, messaging clarity, and competitive differentiation. "
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Title Tag: {{ title }}\n"
        "Meta Tags: {{ metas }}\n"
        "H1 Headings: {{ h1s }}\n\n"
        "DOM Snapshot (truncated to {{ dom_chars }} chars):\n"
        "----\n{{ dom_snippet }}\n----\n\n"
        "Return a JSON object with: score (0-100), summary (one sentence), "
        "issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        html = ctx.inputs.get("dom_html") or ""
        snippet = html[:6000]
        return {
            "target_url": meta.get("url", ""),
            "title": meta.get("title", ""),
            "metas": json.dumps(meta.get("metas", {}), ensure_ascii=False),
            "h1s": json.dumps(meta.get("h1s", []), ensure_ascii=False),
            "dom_snippet": snippet,
            "dom_chars": len(snippet),
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
        return AgentResult.ok(self.role, business_report=payload)
