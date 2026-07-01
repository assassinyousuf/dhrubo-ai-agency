"""`PlannerAgent` — produces a structured task plan from a user request.

M2: deterministic. M4+: graduated to LLM-backed planning once we have a
real reviewer-fleet to reason about.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent


class PlanStep(BaseModel):
    """A single step in the audit plan."""

    id: str
    description: str
    agent_role: str


class PlannerOutput(BaseModel):
    """The structured plan returned by the planner."""

    target_url: str
    steps: list[PlanStep] = Field(default_factory=list)
    notes: str = ""


_PLANNER_DETERMINISTIC = "_use_deterministic_planner"


class PlannerAgent(BaseAgent):
    role: ClassVar[str] = "planner"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url",)
    output_keys: ClassVar[tuple[str, ...]] = ("plan",)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        target_url = str(ctx.inputs.get("target_url", ""))
        if not target_url:
            return AgentResult.fail(self.role, error="missing target_url")

        plan = PlannerOutput(
            target_url=target_url,
            steps=[
                PlanStep(id="crawl", description="Fetch the target URL and extract DOM signals.", agent_role="website_crawler"),
                PlanStep(id="seo_review", description="Audit the page for SEO issues.", agent_role="seo_reviewer"),
                PlanStep(id="report", description="Aggregate sub-reports into a final Markdown document.", agent_role="report_writer"),
                PlanStep(id="export", description="Persist report to disk.", agent_role="exporter"),
            ],
            notes="M2 deterministic plan; LLM-driven planning lands in M4.",
        )
        return AgentResult.ok(self.role, plan=plan.model_dump())
