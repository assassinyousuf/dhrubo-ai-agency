from pathlib import Path

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.screenshot_agent import ScreenshotAgent


async def test_screenshot_agent_writes_files(tmp_path: Path) -> None:
    agent = ScreenshotAgent(output_root=tmp_path)
    ctx = AgentContext(role=agent.role, inputs={"target_url": "https://example.com/"})
    res = await agent.execute(ctx)
    assert res.success is True
    shots = res.outputs["screenshot_paths"]
    assert len(shots) >= 1
    for shot in shots:
        path = Path(shot["path"])
        assert path.exists()


async def test_screenshot_agent_missing_url_fails() -> None:
    agent = ScreenshotAgent()
    ctx = AgentContext(role=agent.role, inputs={})
    res = await agent.execute(ctx)
    assert res.success is False
    assert "missing" in (res.error or "")
