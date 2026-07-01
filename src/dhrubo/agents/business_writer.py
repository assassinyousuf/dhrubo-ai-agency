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
        "You are a master digital agency CEO and copywriter. You are reviewing the "
        "raw JSON audit of a target website. Your goal is to write multiple business "
        "documents based strictly on the flaws found in the audit."
        "\n\n"
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
        # We only pass a heavily stripped version to avoid blowing the context window
        stripped = {}
        for k, v in sub_reports.items():
            if isinstance(v, dict):
                # Only keep score, summary, and high severity issues
                issues = v.get("issues", [])
                high_sev = [i for i in issues if i.get("severity") in ("critical", "major")]
                stripped[k] = {
                    "score": v.get("score"),
                    "summary": v.get("summary"),
                    "critical_issues": high_sev
                }
            else:
                stripped[k] = v

        return {
            "audit_json": json.dumps(stripped, ensure_ascii=False)[:10000]
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
