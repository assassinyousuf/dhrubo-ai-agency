
import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.report_writer import ReportWriterAgent
from dhrubo.agents.website_crawler import WebsiteCrawlerAgent


@pytest.mark.network
async def test_website_crawler_fetches_example() -> None:
    agent = WebsiteCrawlerAgent()
    ctx = AgentContext(role=agent.role, inputs={"target_url": "https://example.com/"})
    res = await agent.execute(ctx)
    assert res.success is True
    meta = res.outputs["page_metadata"]
    assert meta["status_code"] == 200
    assert "Example Domain" in meta["title"]
    assert res.outputs["dom_html"]


async def test_report_writer_renders_seo_section() -> None:
    agent = ReportWriterAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://example.com/",
            "page_metadata": {
                "url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "title": "Example Domain",
                "h1s": ["Example Domain"],
                "metas": {"description": "Example desc"},
                "links_count": 1,
                "images_count": 0,
                "images_without_alt": 0,
                "word_count": 100,
            },
            "seo_report": {
                "score": 72,
                "summary": "Decent baseline.",
                "issues": [
                    {
                        "severity": "critical",
                        "title": "Missing meta description",
                        "detail": "...",
                        "recommendation": "...",
                    },
                    {
                        "severity": "major",
                        "title": "Title too short",
                        "detail": "...",
                        "recommendation": "...",
                    },
                ],
            },
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    assert "Example Domain" in md
    assert "Score" in md
    # Critical issues come before major ones.
    crit_pos = md.find("Missing meta description")
    major_pos = md.find("Title too short")
    assert crit_pos < major_pos
    assert "🔴 Critical" in md


async def test_report_writer_handles_empty_seo() -> None:
    agent = ReportWriterAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://x/",
            "page_metadata": {
                "url": "https://x/", "final_url": "https://x/", "status_code": 200,
                "title": "", "h1s": [], "metas": {}, "links_count": 0,
                "images_count": 0, "images_without_alt": 0, "word_count": 0,
            },
            "seo_report": {"score": 100, "summary": "", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    assert "No SEO issues detected" in md
