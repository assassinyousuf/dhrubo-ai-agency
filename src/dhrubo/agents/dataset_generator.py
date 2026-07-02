"""`DatasetGeneratorAgent` — prepares fine-tuning data from successful audit runs.

Transforms the inputs and outputs into OpenAI/HuggingFace JSONL formats for 
future model fine-tuning (SLM distillation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger

_log = get_logger("agents.dataset_generator")


class DatasetGeneratorAgent(BaseAgent):
    role: ClassVar[str] = "dataset_generator"
    input_keys: ClassVar[tuple[str, ...]] = (
        "sub_reports",
        "business_documents",
        "target_url"
    )
    output_keys: ClassVar[tuple[str, ...]] = ("dataset_stats",)

    def __init__(self, dataset_dir: Path | None = None) -> None:
        self._dir = dataset_dir or Path("./dataset")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        sub_reports = ctx.inputs.get("sub_reports") or {}
        business_docs = ctx.inputs.get("business_documents") or {}
        target = ctx.inputs.get("target_url") or "unknown"

        self._dir.mkdir(parents=True, exist_ok=True)
        out_file = self._dir / "training_data.jsonl"

        written = 0

        # 1. Distill reviewer data
        for lens, report in sub_reports.items():
            if not isinstance(report, dict):
                continue

            # Reconstruct what the user prompt looked like (approx)
            prompt = f"Analyze website {target} for {lens}. Output a JSON report."

            row = {
                "messages": [
                    {"role": "system", "content": "You are a specialized website reviewer."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(report, ensure_ascii=False)}
                ]
            }

            with out_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

        # 2. Distill business writing data
        if business_docs:
            prompt = f"Generate business documents based on this audit summary for {target}."
            row = {
                "messages": [
                    {"role": "system", "content": "You are a master digital agency CEO."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(business_docs, ensure_ascii=False)}
                ]
            }
            with out_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

        return AgentResult.ok(self.role, dataset_stats={"rows_added": written})
