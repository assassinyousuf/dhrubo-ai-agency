import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.website_crawler import WebsiteCrawlerAgent


@pytest.mark.network
async def test_crawler_uses_http_by_default() -> None:
    agent = WebsiteCrawlerAgent()
    ctx = AgentContext(role=agent.role, inputs={"target_url": "https://example.com/"})
    res = await agent.execute(ctx)
    assert res.success is True
    meta = res.outputs["page_metadata"]
    assert meta["render_mode"] == "http"
    assert "Example Domain" in meta["title"]


async def test_crawler_missing_url_fails() -> None:
    agent = WebsiteCrawlerAgent()
    ctx = AgentContext(role=agent.role, inputs={})
    res = await agent.execute(ctx)
    assert res.success is False
