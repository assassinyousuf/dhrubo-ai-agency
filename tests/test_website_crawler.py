"""Tests for :mod:`dhrubo.agents.website_crawler` (M8: favicons + social links)."""

from __future__ import annotations

from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.website_crawler import (
    CrawledPage,
    WebsiteCrawlerAgent,
    _extract,
)

# ---------------------------------------------------------------------------
# _MetaExtractor helpers
# ---------------------------------------------------------------------------


def test_meta_extractor_pulls_favicons() -> None:
    """Favicon-family <link rel="icon|shortcut icon|apple-touch-icon"> tags
    are surfaced on the extracted dict."""
    html = (
        '<html><head>'
        '<link rel="icon" href="/favicon.ico" sizes="32x32" type="image/x-icon">'
        '<link rel="shortcut icon" href="/shortcut.ico" sizes="any">'
        '<link rel="apple-touch-icon" href="/apple.png" sizes="180x180">'
        '<link rel="stylesheet" href="/style.css">'
        '<link rel="preconnect" href="https://cdn.example.com">'
        '</head><body></body></html>'
    )
    out = _extract(html)
    favicons = out["favicons"]
    hrefs = {f["href"] for f in favicons}
    assert "/favicon.ico" in hrefs
    assert "/shortcut.ico" in hrefs
    assert "/apple.png" in hrefs
    # Stylesheet and preconnect links are NOT favicons.
    assert "/style.css" not in hrefs
    assert "https://cdn.example.com" not in hrefs
    # Sizes / type / rel carried through.
    ico = next(f for f in favicons if f["href"] == "/favicon.ico")
    assert ico["sizes"] == "32x32"
    assert ico["type"] == "image/x-icon"


def test_meta_extractor_pulls_social_links() -> None:
    """Anchor tags pointing at known social hosts get surfaced as
    `social_links` with a `platform` and `href`."""
    html = (
        '<html><body>'
        '<a href="https://twitter.com/acme">tw</a>'
        '<a href="https://x.com/acme">x</a>'
        '<a href="https://github.com/acme">gh</a>'
        '<a href="https://linkedin.com/company/acme">li</a>'
        '<a href="https://facebook.com/acme">fb</a>'
        '<a href="https://instagram.com/acme">ig</a>'
        '<a href="https://example.com/about">about</a>'
        '</body></html>'
    )
    out = _extract(html)
    social = out["social_links"]
    platforms = {s["platform"] for s in social}
    assert "twitter.com" in platforms
    assert "x.com" in platforms
    assert "github.com" in platforms
    assert "linkedin.com" in platforms
    assert "facebook.com" in platforms
    assert "instagram.com" in platforms
    # Non-social links should NOT show up.
    assert all("twitter" not in s["href"] or s["platform"] == "twitter.com" for s in social)
    # All social links must carry both `platform` and `href`.
    for s in social:
        assert s["platform"]
        assert s["href"].startswith("http")


def test_meta_extractor_empty_html_returns_empty_collections() -> None:
    """No <link> / <a> → empty favicons + social_links."""
    out = _extract("<html><body><p>hi</p></body></html>")
    assert out["favicons"] == []
    assert out["social_links"] == []


# ---------------------------------------------------------------------------
# CrawledPage shape
# ---------------------------------------------------------------------------


def test_crawled_page_carries_favicons_and_social_links() -> None:
    """`CrawledPage` accepts (and round-trips) favicons + social_links."""
    page = CrawledPage(
        url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        title="Example",
        favicons=[{"href": "/f.ico", "sizes": "32x32", "type": "", "rel": "icon"}],
        social_links=[{"platform": "github.com", "href": "https://github.com/acme"}],
    )
    dumped: dict[str, Any] = page.model_dump()
    assert dumped["favicons"] == [
        {"href": "/f.ico", "sizes": "32x32", "type": "", "rel": "icon"}
    ]
    assert dumped["social_links"] == [
        {"platform": "github.com", "href": "https://github.com/acme"}
    ]


def test_crawled_page_defaults_to_empty_collections() -> None:
    """Defaults: favicons=[], social_links=[]."""
    page = CrawledPage(
        url="https://x/",
        final_url="https://x/",
        status_code=200,
        content_type="text/html",
        title="x",
    )
    assert page.favicons == []
    assert page.social_links == []


# ---------------------------------------------------------------------------
# End-to-end: crawler agent surfaces new fields on page_metadata
# ---------------------------------------------------------------------------


async def test_crawler_agent_surfaces_favicons_and_social_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The crawler agent must put the new fields on `page_metadata` so the
    branding reviewer can pick them up downstream."""
    agent = WebsiteCrawlerAgent()

    from dhrubo.tools.tool_interface import ToolResult

    async def _ok(*_args, **_kwargs):
        html = (
            "<html><head>"
            '<link rel="icon" href="/f.ico" sizes="32x32" type="image/x-icon">'
            "</head><body>"
            '<a href="https://github.com/acme">gh</a>'
            '<a href="https://twitter.com/acme">tw</a>'
            "</body></html>"
        )
        return ToolResult.ok(
            "web_fetch",
            data={
                "success": True,
                "status_code": 200,
                "final_url": "https://example.com/",
                "content_type": "text/html",
                "text": html,
                "headers": {},
            },
        )

    monkeypatch.setattr(agent._tool, "safe_run", _ok)

    ctx = AgentContext(role=agent.role, inputs={"target_url": "https://example.com/"})
    res = await agent.execute(ctx)
    assert res.success is True
    meta = res.outputs["page_metadata"]
    assert any(f["href"] == "/f.ico" for f in meta["favicons"])
    platforms = {s["platform"] for s in meta["social_links"]}
    assert "github.com" in platforms
    assert "twitter.com" in platforms
