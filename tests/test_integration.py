"""End-to-end integration test (gated on OPENAI_API_KEY).

Skipped automatically if the env var is missing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dhrubo import agents as _agents  # noqa: F401  registers all
from dhrubo.commands.cli import register_configured_exporter
from dhrubo.llm.openai_provider import OpenAICompatibleProvider
from dhrubo.workflows.engine import WorkflowEngine, WorkflowStatus
from dhrubo.workflows.website_audit_pipeline import build_website_audit_workflow

pytestmark = pytest.mark.skipif(
    "OPENAI_API_KEY" not in os.environ,
    reason="OPENAI_API_KEY not set — skipping live integration test",
)


async def test_full_audit_pipeline(tmp_path: Path) -> None:
    register_configured_exporter(tmp_path)
    provider = OpenAICompatibleProvider()
    workflow = build_website_audit_workflow()

    engine = WorkflowEngine(max_concurrency=2)
    result = await engine.run(
        workflow,
        initial_inputs={"target_url": "https://example.com/"},
        llm=provider,
    )
    # The crawler hits the real internet. The SEO reviewer hits the real LLM.
    # Either failing still produces a useful PARTIAL result; we accept both.
    assert result.status in (WorkflowStatus.COMPLETED, WorkflowStatus.PARTIAL)
    paths = result.task_results["export"].outputs.get("export_paths", {})
    assert Path(paths["report_md"]).exists()
    report_text = Path(paths["report_md"]).read_text(encoding="utf-8")
    assert "Website Audit Report" in report_text
