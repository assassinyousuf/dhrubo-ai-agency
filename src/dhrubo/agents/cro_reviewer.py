"""`CroReviewerAgent` — analyzes CTAs, lead capture, and conversion funnels."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class CroIssue(BaseModel):
    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class CroReport(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[CroIssue] = Field(default_factory=list)


class CroReviewerAgent(LLMAgent):
    role: ClassVar[str] = "cro_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("page_metadata", "dom_html")
    output_keys: ClassVar[tuple[str, ...]] = ("cro_report",)
    response_model: ClassVar[type[BaseModel]] = CroReport

    system_template: ClassVar[str] = (
        "You are an expert Conversion Rate Optimization (CRO) consultant. "
        "Analyze the provided website data to evaluate CTAs, lead capture forms, "
        "value proposition visibility, and conversion friction. "
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Emails detected: {{ emails }}\n"
        "Phone numbers detected: {{ phones }}\n"
        "Social Links: {{ social }}\n"
        "Technologies used: {{ tech }}\n\n"
        "DOM Snapshot (truncated to {{ dom_chars }} chars):\n"
        "----\n{{ dom_snippet }}\n----\n\n"
        "Return a JSON object with: score (0-100), summary (one sentence), "
        "issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        html = ctx.inputs.get("dom_html") or ""
        snippet = html[:8000]
        return {
            "target_url": meta.get("url", ""),
            "emails": json.dumps(meta.get("emails", []), ensure_ascii=False),
            "phones": json.dumps(meta.get("phone_numbers", []), ensure_ascii=False),
            "social": json.dumps(meta.get("social_links", []), ensure_ascii=False),
            "tech": json.dumps(meta.get("technologies", []), ensure_ascii=False),
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
        return AgentResult.ok(self.role, cro_report=payload)
