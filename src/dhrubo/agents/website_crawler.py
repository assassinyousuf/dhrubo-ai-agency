"""`WebsiteCrawlerAgent` — fetches a target URL and extracts minimal DOM signals.

M3 behavior:

- If ``DHRUBO_USE_REAL_BROWSER`` is set AND the Playwright driver is
  available, drive a headless Chromium to render JavaScript and produce
  the DOM. Otherwise fall back to the lightweight :class:`WebFetchTool`.
- On browser failure, automatically fall back to the HTTP path so the
  pipeline never silently drops a site.
- In both cases, extract title/meta/h1/links/images/word-count via the
  stdlib parser.
"""

from __future__ import annotations

import os
from html.parser import HTMLParser
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.tool_interface import ToolContext
from dhrubo.tools.web_fetch_tool import WebFetchTool

_log = get_logger("agents.crawler")


class _MetaExtractor(HTMLParser):
    """Tiny stdlib parser that collects title/meta/h1/link/word density."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.in_title: bool = False
        self.h1s: list[str] = []
        self._current_h1: list[str] | None = None
        self.metas: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.words: int = 0
        self._in_text: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "h1":
            self._current_h1 = []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            content = a.get("content") or ""
            if name and content:
                self.metas[name] = content
        elif tag == "a":
            href = a.get("href", "")
            if href:
                self.links.append({"href": href, "text": ""})
        elif tag == "img":
            src = a.get("src", "")
            alt = a.get("alt", "")
            if src:
                self.images.append({"src": src, "alt": alt})
        elif tag in ("p", "li", "span", "div"):
            self._in_text = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "h1" and self._current_h1 is not None:
            self.h1s.append(" ".join("".join(self._current_h1).split()))
            self._current_h1 = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self._current_h1 is not None:
            self._current_h1.append(data)
        if self._in_text:
            self.words += len(data.split())


class CrawledPage(BaseModel):
    """Structured representation of a crawled page."""

    url: str
    final_url: str
    status_code: int
    content_type: str
    title: str
    h1s: list[str] = Field(default_factory=list)
    metas: dict[str, str] = Field(default_factory=dict)
    links_count: int = 0
    images_count: int = 0
    images_without_alt: int = 0
    word_count: int = 0
    render_mode: str = "http"  # "http" | "browser"


def _extract(html: str) -> dict[str, Any]:
    parser = _MetaExtractor()
    parser.feed(html)
    images_without_alt = sum(1 for img in parser.images if not img["alt"].strip())
    return {
        "title": parser.title.strip(),
        "h1s": parser.h1s,
        "metas": parser.metas,
        "links": parser.links,
        "images": parser.images,
        "word_count": parser.words,
        "images_without_alt": images_without_alt,
    }


def _use_real_browser() -> bool:
    if os.environ.get("DHRUBO_USE_REAL_BROWSER", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        from dhrubo.tools.null_driver import _DRIVERS

        return "playwright" in _DRIVERS
    except Exception:
        return False


class WebsiteCrawlerAgent(BaseAgent):
    role: ClassVar[str] = "website_crawler"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url",)
    output_keys: ClassVar[tuple[str, ...]] = ("dom_html", "page_metadata")
    required_tools: ClassVar[tuple[str, ...]] = ("web_fetch",)

    def __init__(self) -> None:
        self._tool = WebFetchTool()

    async def _fetch_via_browser(self, url: str) -> dict[str, Any] | None:
        """Try the browser path. Returns None on failure (fallback expected)."""
        try:
            from dhrubo.tools.null_driver import get_driver

            async with get_driver("playwright") as driver:
                snap = await driver.navigate(url)
                return {
                    "url": url,
                    "final_url": snap.final_url,
                    "status_code": snap.status_code,
                    "content_type": "text/html",
                    "title": snap.title,
                    "html": snap.html,
                }
        except Exception as exc:
            _log.warning("crawler.browser_failed", extra={"url": url, "error": str(exc)})
            return None

    async def _fetch_via_http(self, url: str, tool_ctx: ToolContext) -> dict[str, Any] | None:
        result = await self._tool.safe_run({"url": url}, tool_ctx)
        if not result.success or result.data is None:
            return None
        return {
            "url": url,
            "final_url": result.data.get("final_url", url),
            "status_code": result.data.get("status_code", 0),
            "content_type": result.data.get("content_type", ""),
            "title": "",  # filled in by _extract
            "html": result.data["text"],
        }

    async def execute(self, ctx: AgentContext) -> AgentResult:
        url = ctx.inputs.get("target_url")
        if not url:
            return AgentResult.fail(self.role, error="missing target_url")

        tool_ctx = ToolContext(requester_role=self.role)
        render_mode = "http"
        page_data: dict[str, Any] | None = None

        if _use_real_browser():
            page_data = await self._fetch_via_browser(str(url))
            if page_data is not None:
                render_mode = "browser"

        if page_data is None:
            page_data = await self._fetch_via_http(str(url), tool_ctx)

        if page_data is None:
            return AgentResult.fail(
                self.role,
                error="both browser and HTTP fetch failed",
            )

        html = page_data["html"]
        extracted = _extract(html)
        page = CrawledPage(
            url=page_data["url"],
            final_url=page_data["final_url"],
            status_code=page_data["status_code"],
            content_type=page_data["content_type"],
            title=extracted["title"] or page_data["title"],
            h1s=extracted["h1s"],
            metas=extracted["metas"],
            links_count=len(extracted["links"]),
            images_count=len(extracted["images"]),
            images_without_alt=extracted["images_without_alt"],
            word_count=extracted["word_count"],
            render_mode=render_mode,
        )

        return AgentResult.ok(
            self.role,
            dom_html=html,
            page_metadata=page.model_dump(),
        )
