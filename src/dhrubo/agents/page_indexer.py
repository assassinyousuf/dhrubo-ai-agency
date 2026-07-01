"""`PageIndexerAgent` — resolves the list of URLs to audit.

M9 introduces multi-page audits. The indexer is the single source
of truth for "how many pages does this audit cover?" and what
their canonical URLs are.

Design notes:

- **Deterministic.** No LLM call. The indexer just normalizes
  whatever list of URLs the workflow hands it.
- **Inputs.** The CLI / engine populate two input keys:
  ``target_urls`` (preferred — a list of strings) and
  ``target_url`` (single-URL shortcut). The indexer prefers
  ``target_urls`` and falls back to a length-1 list wrapping
  ``target_url``.
- **Outputs.**
  - ``pages``: list of dicts ``{index, url, slug}``.
  - ``seed_domain``: the canonical host (e.g. ``example.com``)
    used as the run-dir slug when multiple pages are audited.
- **Backward-compat.** A single URL produces the same shape —
  one entry in ``pages`` — so downstream consumers (the report
  writer and the exporter) can rely on a uniform contract.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.core.logger import get_logger
from dhrubo.core.slug import safe_slug

_log = get_logger("agents.page_indexer")


class Page(BaseModel):
    """One entry in the audit's URL list."""

    index: int = Field(ge=0)
    url: str
    slug: str


class PageIndex(BaseModel):
    """The audit's URL list + canonical seed domain."""

    pages: list[Page]
    seed_domain: str


class PageIndexerAgent(BaseAgent):
    role: ClassVar[str] = "page_indexer"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url", "target_urls")
    output_keys: ClassVar[tuple[str, ...]] = ("pages", "seed_domain")

    async def execute(self, ctx: AgentContext) -> AgentResult:
        # Prefer the explicit list; fall back to a length-1 wrap of target_url.
        urls_raw: Any = ctx.inputs.get("target_urls")
        if isinstance(urls_raw, (list, tuple)) and urls_raw:
            urls = [str(u).strip() for u in urls_raw if str(u).strip()]
        else:
            single = ctx.inputs.get("target_url")
            urls = [str(single).strip()] if single else []

        if not urls:
            return AgentResult.fail(
                self.role,
                error="no URLs provided (target_urls and target_url both empty)",
            )

        # Compute seed domain from the first URL.
        try:
            netloc = urlparse(urls[0]).netloc
        except Exception:
            netloc = ""
        seed_domain = netloc.lower().removeprefix("www.") or "report"

        pages = [
            Page(index=i, url=u, slug=safe_slug(u)).model_dump()
            for i, u in enumerate(urls)
        ]

        _log.info(
            "page_indexer.resolved",
            extra={"role": self.role, "count": len(pages), "seed_domain": seed_domain},
        )

        return AgentResult.ok(
            self.role,
            pages=pages,
            seed_domain=seed_domain,
        )


__all__ = ["Page", "PageIndex", "PageIndexerAgent"]
