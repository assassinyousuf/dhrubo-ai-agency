
import pytest
from dhrubo.tools.tool_interface import ToolContext
from dhrubo.tools.web_fetch_tool import WebFetchTool


@pytest.mark.network
async def test_web_fetch_returns_html() -> None:
    tool = WebFetchTool()
    result = await tool.safe_run(
        {"url": "https://example.com/", "max_bytes": 50_000},
        ToolContext(requester_role="test"),
    )
    assert result.success is True
    data = result.data
    assert data["status_code"] == 200
    assert "<html" in data["text"].lower()


async def test_web_fetch_rejects_bad_params() -> None:
    tool = WebFetchTool()
    result = await tool.safe_run({"url": ""}, ToolContext(requester_role="t"))
    assert result.success is False
    assert "url" in (result.error or "")


async def test_web_fetch_handles_404() -> None:
    tool = WebFetchTool()
    result = await tool.safe_run(
        {"url": "https://example.com/this-does-not-exist-12345", "max_bytes": 1024},
        ToolContext(requester_role="t"),
    )
    assert result.success is False
    assert result.data is not None  # body still returned for inspection
    assert "HTTP" in (result.error or "")
