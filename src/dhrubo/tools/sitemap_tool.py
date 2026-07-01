"""Sitemap and Robots.txt Tool.

Fetches and parses robots.txt and sitemap.xml to extract SEO rules and page indices.
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from dhrubo.core.logger import get_logger
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult
from dhrubo.tools.web_fetch_tool import WebFetchTool, WebFetchParams

_log = get_logger("tools.sitemap")

class SitemapParams(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

class SitemapTool(Tool[SitemapParams]):
    """Fetches robots.txt and checks for sitemap existence."""

    name: ClassVar[str] = "sitemap_robot_fetcher"
    description: ClassVar[str] = "Fetch and analyze robots.txt and sitemaps for SEO context."
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Base URL of the site"),
    )
    params_model: ClassVar[type[BaseModel]] = SitemapParams

    async def run(self, params: SitemapParams, ctx: ToolContext) -> ToolResult:
        parsed = urlparse(params.url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        robots_url = f"{base_url}/robots.txt"
        sitemap_url = f"{base_url}/sitemap.xml"
        
        _log.info(f"Fetching robots.txt for {base_url}")
        fetch_tool = WebFetchTool()
        
        robots_data = {"exists": False, "content": ""}
        sitemap_data = {"exists": False, "discovered_urls": []}
        
        # Fetch robots
        try:
            res = await fetch_tool.run(WebFetchParams(url=robots_url, timeout_seconds=10.0), ctx)
            if res.success and "data" in res.data and res.data["data"]["status_code"] == 200:
                text = res.data["data"]["text"]
                robots_data["exists"] = True
                robots_data["content"] = text
                
                # Check for sitemap directives
                for line in text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap_url = line.split(":", 1)[1].strip()
        except Exception as e:
            _log.warning(f"Failed to fetch robots.txt: {e}")
            
        # Fetch sitemap
        try:
            s_res = await fetch_tool.run(WebFetchParams(url=sitemap_url, timeout_seconds=10.0), ctx)
            if s_res.success and "data" in s_res.data and s_res.data["data"]["status_code"] == 200:
                sitemap_data["exists"] = True
                # Just flag it, full XML parsing is too heavy for now
        except Exception as e:
            _log.warning(f"Failed to fetch sitemap: {e}")
            
        return ToolResult.ok(
            self.name, 
            data={
                "robots": robots_data,
                "sitemap_url": sitemap_url if sitemap_data["exists"] else None,
                "has_sitemap": sitemap_data["exists"]
            }
        )
