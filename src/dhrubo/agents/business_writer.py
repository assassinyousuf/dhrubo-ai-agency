"""`BusinessWriterAgent` — generates specialized business documents from the audit data."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class BusinessDocuments(BaseModel):
    executive_summary: str = Field(description="A high-level markdown summary for executives.")
    proposal: str = Field(description="A markdown business proposal addressing the issues.")
    cold_email: str = Field(description="A plain text cold email pitching your agency to fix the site.")
    roadmap: str = Field(description="A markdown 90-day technical roadmap to fix the site.")


class BusinessWriterAgent(LLMAgent):
    role: ClassVar[str] = "business_writer"

    input_keys: ClassVar[tuple[str, ...]] = ("sub_reports",)
    output_keys: ClassVar[tuple[str, ...]] = ("business_documents",)
    response_model: ClassVar[type[BaseModel]] = BusinessDocuments

    system_template: ClassVar[str] = (
        "You are a master digital agency CEO and elite technical copywriter. You are reviewing the "
        "raw JSON audit of a target website. Your goal is to write multiple business "
        "documents based strictly on the flaws found in the audit.\n\n"
        "**CRITICAL INSTRUCTION: You must be extremely detailed, professional, and thorough.**\n"
        "- Do NOT write brief, single-sentence summaries.\n"
        "- Use multi-paragraph explanations, actionable bullet points, specific strategies, and deep insights.\n"
        "- For the Proposal, structure it like a premium consulting pitch with sections for Executive Summary, Problem Definition, Proposed Solutions, and Expected Outcomes.\n"
        "- For the Roadmap, provide a detailed 90-day technical plan broken into 30-day sprints with specific tasks.\n\n"
        "Output ONLY a JSON object matching the provided schema. The string fields should "
        "contain formatting (Markdown for proposal/summary/roadmap, plain text for cold email)."
    )

    user_template: ClassVar[str] = (
        "Here are the sub-reports from the technical audit:\n"
        "{{ audit_json }}\n\n"
        "Generate the executive_summary, proposal, cold_email, and roadmap."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        sub_reports = ctx.inputs.get("sub_reports") or {}
        # Pass the full sub_reports, just trim any massive string blobs if they exist
        stripped = {}
        for k, v in sub_reports.items():
            if isinstance(v, dict):
                # Keep everything, but remove massive raw HTML or Base64 screenshots if they accidentally bleed in
                clean_v = {key: val for key, val in v.items() if key not in ("raw_html", "screenshot_base64", "dom_snapshot")}
                stripped[k] = clean_v
            else:
                stripped[k] = v

        return {
            "audit_json": json.dumps(stripped, ensure_ascii=False)[:30000]
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
        return AgentResult.ok(self.role, business_documents=payload)
