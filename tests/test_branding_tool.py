"""Tests for :mod:`dhrubo.tools.branding_tool`."""

from __future__ import annotations

import pytest
from dhrubo.tools.branding_tool import (
    BrandingParams,
    BrandingTool,
    _extract_brand,
    _extract_brand_colors,
    _extract_social_links,
)
from dhrubo.tools.tool_interface import ToolContext

# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_extract_brand_colors_from_inline_style() -> None:
    html = (
        "<style>:root { --brand-color: #0a0a0a; --accent: #3b82f6; } "
        ".btn { color: #fff; background: #0a0a0a; } "
        "body { background-color: #ffffff; }</style>"
    )
    colors = _extract_brand_colors(html)
    assert "#0a0a0a" in colors
    assert "#ffffff" in colors
    # Repeated values are deduped.
    assert len(colors) == len(set(colors))


def test_extract_brand_colors_normalizes_short_hex() -> None:
    html = (
        "<style>:root { --brand-color: #abc; --brand-bg: #abcd; }</style>"
    )
    colors = _extract_brand_colors(html)
    # #abc → #aabbcc, #abcd → #aabbccdd
    assert "#aabbcc" in colors
    assert "#aabbccdd" in colors


def test_extract_brand_colors_empty_html() -> None:
    assert _extract_brand_colors("") == []
    assert _extract_brand_colors("<p>no style here</p>") == []


def test_extract_social_links_from_metadata() -> None:
    page_metadata = {
        "social_links": [
            {"platform": "twitter.com", "href": "https://twitter.com/x"},
            {"platform": "github.com", "href": "https://github.com/y"},
        ]
    }
    out = _extract_social_links(page_metadata, html="")
    assert len(out) == 2
    assert out[0]["platform"] == "twitter"


def test_extract_social_links_dedupes() -> None:
    page_metadata = {
        "social_links": [
            {"platform": "twitter.com", "href": "https://twitter.com/x"},
            {"platform": "twitter.com", "href": "https://twitter.com/x"},
        ]
    }
    out = _extract_social_links(page_metadata, html="")
    assert len(out) == 1


def test_extract_social_links_from_html() -> None:
    page_metadata = {}
    html = (
        '<a href="https://twitter.com/foo">tw</a>'
        '<a href="https://example.com/">ex</a>'
        '<a href="https://github.com/bar">gh</a>'
    )
    out = _extract_social_links(page_metadata, html=html)
    assert len(out) == 2
    platforms = {s["platform"] for s in out}
    assert "twitter" in platforms
    assert "github" in platforms


def test_extract_brand_prefers_og_image() -> None:
    page_metadata = {
        "metas": {"og:image": "https://example.com/og.png"},
        "favicons": [{"href": "/f.ico", "sizes": "32x32", "type": "", "rel": "icon"}],
    }
    snap = _extract_brand(page_metadata, html="")
    assert snap["logo_url"] == "https://example.com/og.png"
    assert snap["og_image"] == "https://example.com/og.png"
    assert snap["theme_color"] is None


def test_extract_brand_falls_back_to_favicon() -> None:
    page_metadata = {
        "favicons": [{"href": "/f.ico", "sizes": "32x32", "type": "", "rel": "icon"}],
    }
    snap = _extract_brand(page_metadata, html="")
    assert snap["logo_url"] == "/f.ico"


# ---------------------------------------------------------------------------
# Tool runtime
# ---------------------------------------------------------------------------


def _tool(monkeypatch: pytest.MonkeyPatch) -> BrandingTool:
    tool = BrandingTool()
    from dhrubo.config.models import RetryConfig

    tool._retry_policy = RetryConfig(
        max_attempts=1, initial_delay_seconds=0.001, max_delay_seconds=0.01, jitter=False
    )
    return tool


async def test_extracts_logo_from_og_image(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {
        "title": "Acme Inc.",
        "metas": {"og:image": "https://acme.test/logo.png", "theme-color": "#0a0a0a"},
        "favicons": [{"href": "/f.ico", "sizes": "32x32", "type": "", "rel": "icon"}],
    }
    params = BrandingParams(
        url="https://acme.test/", page_metadata=page_metadata, dom_html=""
    )
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    assert res.data["skipped"] is False
    assert res.data["logo_url"] == "https://acme.test/logo.png"
    assert res.data["theme_color"] == "#0a0a0a"


async def test_extracts_brand_colors_from_inline_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {"title": "X"}
    html = "<style>:root { --brand-color: #1da1f2; --accent: #0a0a0a; }</style>"
    params = BrandingParams(url="https://x/", page_metadata=page_metadata, dom_html=html)
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    assert "#1da1f2" in res.data["brand_colors"]


async def test_extracts_social_links(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {
        "title": "X",
        "social_links": [
            {"platform": "twitter.com", "href": "https://twitter.com/acme"},
            {"platform": "github.com", "href": "https://github.com/acme"},
        ],
    }
    params = BrandingParams(url="https://x/", page_metadata=page_metadata, dom_html="")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    assert len(res.data["social_links"]) == 2


async def test_flags_missing_logo_as_major(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {
        "title": "X",
        "metas": {},
        "favicons": [],
        "social_links": [],
    }
    params = BrandingParams(url="https://x/", page_metadata=page_metadata, dom_html="")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert checks_by_id["no-logo"]["severity"] == "major"


async def test_flags_low_social_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {
        "title": "X",
        "metas": {"og:image": "https://x/logo.png"},
        "favicons": [],
        "social_links": [],
    }
    params = BrandingParams(url="https://x/", page_metadata=page_metadata, dom_html="")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert checks_by_id["low-social-presence"]["severity"] == "minor"


async def test_title_consistency_check(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    page_metadata = {
        "title": "Page Title",
        "metas": {
            "og:image": "https://x/logo.png",
            "og:title": "Completely Different",
        },
    }
    params = BrandingParams(url="https://x/", page_metadata=page_metadata, dom_html="")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    checks_by_id = {c["id"]: c for c in res.data["checks"]}
    assert "title-inconsistent" in checks_by_id
    assert checks_by_id["title-inconsistent"]["severity"] == "minor"


async def test_skips_when_no_metadata_no_html(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)

    async def _fail(**_kw):
        return {"success": False, "error": "offline", "html": "", "final_url": "https://x/"}

    monkeypatch.setattr(tool, "_do_call", _fail)
    params = BrandingParams(url="https://x/", page_metadata={}, dom_html="")
    res = await tool.safe_run(params.model_dump(), ToolContext(requester_role="branding"))
    assert res.success is True
    assert res.data["skipped"] is True
    assert res.data["checks_count"] == 0


async def test_rejects_empty_url(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _tool(monkeypatch)
    res = await tool.safe_run({"url": ""}, ToolContext(requester_role="branding"))
    assert res.success is False
    assert "Invalid params" in (res.error or "")


def test_is_available_returns_true() -> None:
    assert BrandingTool.is_available() is True
