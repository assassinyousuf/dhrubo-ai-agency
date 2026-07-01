"""`ExporterAgent` — writes the final report to disk (and optionally other formats)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent


def _safe_dir_name(url: str) -> str:
    """Make a filesystem-safe slug from a URL."""
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    slug = "".join(c if c in keep else "_" for c in url)
    return slug[:80] or "report"


class ExporterAgent(BaseAgent):
    role: ClassVar[str] = "exporter"
    input_keys: ClassVar[tuple[str, ...]] = ("final_report_md", "target_url")
    output_keys: ClassVar[tuple[str, ...]] = ("export_paths",)

    def __init__(self, output_root: Path | None = None) -> None:
        self._root = output_root or Path("./output")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        report: str = ctx.inputs.get("final_report_md") or ""
        target_url = ctx.inputs.get("target_url") or "report"

        ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self._root / f"{ts}_{_safe_dir_name(str(target_url))}"
        run_dir.mkdir(parents=True, exist_ok=True)

        report_path = run_dir / "report.md"
        data_path = run_dir / "data.json"

        report_path.write_text(report, encoding="utf-8")
        payload = {
            "target_url": str(target_url),
            "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "report_markdown": report,
            "context_metadata": dict(ctx.metadata or {}),
        }
        data_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        return AgentResult.ok(
            self.role,
            export_paths={
                "report_md": str(report_path),
                "data_json": str(data_path),
                "run_dir": str(run_dir),
            },
        )
