"""Tests for :mod:`dhrubo.agents.page_indexer`."""

from __future__ import annotations

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.page_indexer import Page, PageIndex, PageIndexerAgent

# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_single_target_url_passthrough() -> None:
    """When only ``target_url`` is set, the indexer emits a length-1 pages list."""
    agent = PageIndexerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={"target_url": "https://example.com/"},
    )
    res = await agent.execute(ctx)
    assert res.success is True
    pages = res.outputs["pages"]
    assert len(pages) == 1
    assert pages[0]["index"] == 0
    assert pages[0]["url"] == "https://example.com/"
    assert pages[0]["slug"]
    assert res.outputs["seed_domain"] == "example.com"


async def test_multi_target_urls_passthrough() -> None:
    agent = PageIndexerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://a/",
            "target_urls": ["https://a/", "https://b/", "https://c/"],
        },
    )
    res = await agent.execute(ctx)
    assert res.success is True
    pages = res.outputs["pages"]
    assert len(pages) == 3
    assert [p["index"] for p in pages] == [0, 1, 2]
    assert [p["url"] for p in pages] == ["https://a/", "https://b/", "https://c/"]
    # Slugs are unique.
    slugs = [p["slug"] for p in pages]
    assert len(set(slugs)) == len(slugs)
    # seed_domain is the host of the first URL.
    assert res.outputs["seed_domain"] == "a"


async def test_preserves_order() -> None:
    agent = PageIndexerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_urls": ["https://z.example/", "https://a.example/", "https://m.example/"],
        },
    )
    res = await agent.execute(ctx)
    assert [p["url"] for p in res.outputs["pages"]] == [
        "https://z.example/",
        "https://a.example/",
        "https://m.example/",
    ]


async def test_strips_www_from_seed_domain() -> None:
    agent = PageIndexerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={"target_url": "https://www.example.com/path/"},
    )
    res = await agent.execute(ctx)
    assert res.outputs["seed_domain"] == "example.com"


async def test_strips_whitespace_in_target_urls() -> None:
    agent = PageIndexerAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={"target_urls": ["  https://a/  ", "", "https://b/"]},
    )
    res = await agent.execute(ctx)
    assert [p["url"] for p in res.outputs["pages"]] == ["https://a/", "https://b/"]


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_fails_when_no_urls() -> None:
    agent = PageIndexerAgent()
    ctx = AgentContext(role=agent.role, inputs={})
    res = await agent.execute(ctx)
    assert res.success is False
    assert "empty" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


def test_page_model_round_trip() -> None:
    p = Page(index=2, url="https://x/", slug="https_x_")
    assert p.model_dump() == {"index": 2, "url": "https://x/", "slug": "https_x_"}


def test_page_index_model_round_trip() -> None:
    idx = PageIndex(
        pages=[Page(index=0, url="https://a/", slug="https_a_")],
        seed_domain="a",
    )
    assert idx.seed_domain == "a"
    assert len(idx.pages) == 1
