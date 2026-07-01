from pathlib import Path

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.exporter import ExporterAgent


async def test_exporter_writes_report_and_data(tmp_path: Path) -> None:
    agent = ExporterAgent(output_root=tmp_path)
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hello\n",
            "target_url": "https://example.com/?q=test",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    report = Path(paths["report_md"])
    data = Path(paths["data_json"])
    assert report.exists()
    assert report.read_text(encoding="utf-8").startswith("# Hello")
    payload = __import__("json").loads(data.read_text(encoding="utf-8"))
    assert payload["target_url"].startswith("https://example.com")
    assert "generated_at" in payload
