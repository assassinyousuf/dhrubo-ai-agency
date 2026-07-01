"""`DiffReviewerAgent` — deterministic diff between two audit runs (M10).

Reads two ``sub_reports`` payloads (current + previous) from
``ctx.inputs``, dispatches them to :class:`dhrubo.tools.diff_tool.DiffTool`,
and emits the diff dict under the ``diff_payload`` output key.

The agent is intentionally deterministic — no LLM call, no retries.
A failed diff is reported as a successful result with
``success=False`` so the engine records the failure in
``result.task_results["diff"]`` but the pipeline can still proceed
to the exporter (which writes an empty ``diff.json`` and the
report keeps going without a diff section).
"""

from __future__ import annotations

from typing import Any, ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.diff_tool import DiffParams, DiffTool
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.diff_reviewer")


class DiffReviewerAgent(BaseAgent):
    """Compute a structured diff between two audit sub-report payloads."""

    role: ClassVar[str] = "diff_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = (
        "previous_sub_reports",
        "sub_reports",
        "diff_against",
        "current_run_id",
    )
    output_keys: ClassVar[tuple[str, ...]] = ("diff_payload",)
    required_tools: ClassVar[tuple[str, ...]] = ("diff",)

    def __init__(self, *, diff_tool: DiffTool | None = None) -> None:
        self._diff_tool = diff_tool or DiffTool()

    async def execute(self, ctx: AgentContext) -> AgentResult:
        previous: dict[str, Any] = ctx.inputs.get("previous_sub_reports") or {}
        current: dict[str, Any] = ctx.inputs.get("sub_reports") or {}
        previous_id: str = (
            ctx.inputs.get("diff_against")
            or previous.get("_run_id")
            or "previous"
        )
        current_id: str = (
            ctx.inputs.get("current_run_id")
            or current.get("_run_id")
            or "current"
        )

        params = DiffParams(
            run_id_a=str(previous_id),
            run_id_b=str(current_id),
            sub_reports_a=previous,
            sub_reports_b=current,
        )

        tool_ctx = ToolContext(requester_role=self.role)
        res = await self._diff_tool.safe_run(
            {
                "run_id_a": params.run_id_a,
                "run_id_b": params.run_id_b,
                "sub_reports_a": params.sub_reports_a,
                "sub_reports_b": params.sub_reports_b,
            },
            tool_ctx,
        )

        if not res.success or not res.data:
            reason = res.error or "diff tool returned no data"
            _log.warning(
                "diff.compute_failed",
                extra={"role": self.role, "reason": reason},
            )
            # Empty payload — renderer treats this as "no changes".
            empty = {
                "run_id_a": params.run_id_a,
                "run_id_b": params.run_id_b,
                "added": [],
                "removed": [],
                "severity_changed": [],
                "score_changed": [],
                "summary": f"diff unavailable: {reason}",
            }
            return AgentResult.ok(self.role, diff_payload=empty)

        _log.info(
            "diff.computed",
            extra={
                "role": self.role,
                "summary": res.data.get("summary", ""),
                "previous_id": params.run_id_a,
                "current_id": params.run_id_b,
            },
        )
        return AgentResult.ok(self.role, diff_payload=res.data)


__all__ = ["DiffReviewerAgent"]
