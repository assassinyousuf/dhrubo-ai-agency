"""`QaReviewerAgent` — validates outputs of other agents to prevent hallucinations."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent


class QaIssue(BaseModel):
    agent_role: str
    issue: str
    severity: str = Field(pattern=r"^(critical|warning)$")


class QaReport(BaseModel):
    passed: bool
    hallucinations_detected: int
    issues: list[QaIssue] = Field(default_factory=list)


class QaReviewerAgent(LLMAgent):
    role: ClassVar[str] = "qa_reviewer"

    # Empty input_keys by default; we intercept in execute or let it be passed in
    input_keys: ClassVar[tuple[str, ...]] = ()
    output_keys: ClassVar[tuple[str, ...]] = ("qa_report",)
    response_model: ClassVar[type[BaseModel]] = QaReport

    system_template: ClassVar[str] = (
        "You are the QA and Guardrail Agent. Your job is to read the JSON outputs generated "
        "by the specialized AI reviewers and detect hallucinations. "
        "Flag if any agent uses placeholder text like '[Insert Company Name]', 'example.com', "
        "or makes statements clearly contradicting another agent. "
        "Output ONLY a JSON object matching the provided schema."
    )

    user_template: ClassVar[str] = (
        "Review the following sub-reports for hallucinations or placeholders:\n"
        "{{ reports_json }}\n\n"
        "Return a JSON object with: passed (boolean), hallucinations_detected (int), "
        "and issues (array of {agent_role, issue, severity})."
    )

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        # Collect all reports dynamically
        reports = {}
        for key, value in ctx.inputs.items():
            if key.endswith("_report") and value:
                reports[key] = value

        return {
            "reports_json": json.dumps(reports, ensure_ascii=False)[:15000] # Cap size to avoid window overflow
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
        # If QA failed, we could raise an AgentError to trigger the DAG retry loop.
        # But for M15, we just record the QA report so it can be viewed.
        return AgentResult.ok(self.role, qa_report=payload)
