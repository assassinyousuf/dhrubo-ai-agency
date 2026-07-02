"""`SeoReviewerAgent` — analyzes a crawled page and emits a structured SEO sub-report."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class SeoIssue(BaseModel):
    """One SEO issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class SeoReport(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[SeoIssue] = Field(default_factory=list)


class SeoReviewerAgent(LLMAgent):
    role: ClassVar[str] = "seo_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("dom_html", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("seo_report",)
    response_model: ClassVar[type[BaseModel]] = SeoReport

    system_template: ClassVar[str] = (
        "You are a senior technical-SEO reviewer. Given a page's DOM and metadata, "
        "produce a structured SEO audit. Focus on: title tag, meta description, "
        "headings hierarchy, image alt attributes, word count, and link hygiene.\n"
        "**CRITICAL INSTRUCTION: You must provide EXTREMELY DETAILED, comprehensive analyses.**\n"
        "- For every issue 'detail', write multiple sentences explaining the SEO impact.\n"
        "- For every 'recommendation', provide a specific, actionable multi-step solution.\n"
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL after redirects: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Meta tags: {{ metas }}\n"
        "H1 headings: {{ h1s }}\n"
        "Links count: {{ links_count }}\n"
        "Images count: {{ images_count }}\n"
        "Images without alt: {{ images_without_alt }}\n"
        "Word count: {{ word_count }}\n\n"
        "DOM (truncated to first {{ dom_chars }} characters):\n"
        "----\n{{ dom_snippet }}\n----\n\n"
        "Return a JSON object with: score (0-100), summary (detailed multi-sentence paragraph), "
        "issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        html = ctx.inputs.get("dom_html") or ""
        snippet = html[:6000]
        return {
            "target_url": meta.get("url", ""),
            "final_url": meta.get("final_url", ""),
            "title": meta.get("title", "") or "(no <title>)",
            "metas": json.dumps(meta.get("metas", {}), ensure_ascii=False),
            "h1s": json.dumps(meta.get("h1s", []), ensure_ascii=False),
            "links_count": meta.get("links_count", 0),
            "images_count": meta.get("images_count", 0),
            "images_without_alt": meta.get("images_without_alt", 0),
            "word_count": meta.get("word_count", 0),
            "dom_snippet": snippet,
            "dom_chars": len(snippet),
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def execute(self, ctx: AgentContext) -> AgentResult:
        try:
            res = await super().execute(ctx)
        except Exception as exc:  # AgentError subclasses already structured
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res
        # Persist the validated payload under the declared output key.
        payload = res.outputs.get("response", {})
        return AgentResult.ok(self.role, seo_report=payload)
