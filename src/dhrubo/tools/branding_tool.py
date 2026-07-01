"""`BrandingTool` — extracts brand identity signals from a page.

Data sources, in order:

1. ``page_metadata`` (already extracted by :class:`WebsiteCrawlerAgent`):
   ``metas`` (incl. ``og:image``, ``twitter:image``, ``theme-color``,
   ``og:title``, ``twitter:title``), ``favicons``, ``social_links``.
2. ``dom_html`` (optional, falls back to a fresh fetch): the full HTML
   for inline ``<style>`` scanning to extract brand colors.

Design notes:

- **No new deps**: regex-based color extraction; ``httpx`` reuses
  :class:`WebFetchTool`.
- **Test seam**: ``_do_call`` is the method tests monkey-patch.
- **Retry policy**: the ``branding_scan`` entry in
  ``config/retry_policies.yaml`` (3 attempts, 1.0s → 10s, jittered).
- **Graceful skip**: when no data is reachable (no metadata AND no
  HTML), the tool returns ``skipped=True`` so the audit never fails.
- **Brand colors are best-effort**: regex over inline ``<style>``
  blocks only. Screenshot-based palette extraction is M9+ work.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from dhrubo.config.loader import load_retry_policies
from dhrubo.core.logger import get_logger
from dhrubo.core.retry import DEFAULT_RETRY, RetryConfig, retry_async
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult
from dhrubo.tools.web_fetch_tool import WebFetchTool

_log = get_logger("tools.branding")

# Hostnames we treat as "social presence".
_SOCIAL_HOSTS: tuple[tuple[str, str], ...] = (
    ("twitter", "twitter.com"),
    ("twitter", "x.com"),
    ("linkedin", "linkedin.com"),
    ("github", "github.com"),
    ("facebook", "facebook.com"),
    ("instagram", "instagram.com"),
    ("youtube", "youtube.com"),
    ("tiktok", "tiktok.com"),
)

# Regex for hex colors (3, 4, 6, 8 chars) in CSS declarations.
_HEX_COLOR_RX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b")
# Capture only inside <style>...</style> blocks.
_STYLE_BLOCK_RX = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
# CSS variable color declarations: --brand-color: #fff;
_CSSVAR_COLOR_RX = re.compile(
    r"--[a-zA-Z][a-zA-Z0-9-]*-?(?:color|bg|background)\s*:\s*(#[0-9a-fA-F]{3,8})",
    re.IGNORECASE,
)
# Plain color declarations: color: #fff; background-color: #000; background: #abc;
_COLOR_DECL_RX = re.compile(
    r"(?:^|[\s;{])(?:color|background(?:-color)?)\s*:\s*(#[0-9a-fA-F]{3,8})\b",
    re.IGNORECASE,
)

_MAX_BRAND_COLORS = 12
_MAX_SOCIAL_LINKS = 20
_MAX_FAVICONS = 12


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``branding_scan`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("branding.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("branding_scan", DEFAULT_RETRY)


class BrandingParams(BaseModel):
    """Inputs for :class:`BrandingTool`."""

    url: str = Field(min_length=1, max_length=2048)
    page_metadata: dict[str, Any] = Field(default_factory=dict)
    dom_html: str | None = None
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)


class BrandingTool(Tool[BrandingParams]):
    """Extract brand identity signals from a page."""

    name: ClassVar[str] = "branding"
    description: ClassVar[str] = (
        "Extract brand identity signals (logo, favicon, OG/Twitter image, "
        "theme color, social presence, inline brand colors) from the page "
        "metadata and inline CSS."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Absolute URL to audit."),
        ToolParameter("page_metadata", "object", required=False),
        ToolParameter("dom_html", "string", required=False),
        ToolParameter("timeout_seconds", "float", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = BrandingParams

    def __init__(
        self,
        *,
        web_fetch_tool: WebFetchTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._web_fetch = web_fetch_tool or WebFetchTool()
        self._retry_policy: RetryConfig = _resolve_retry_policy(config_dir)

    @staticmethod
    def is_available() -> bool:
        """``httpx`` is a core dep, so branding is always available."""
        return True

    async def _do_call(
        self,
        *,
        url: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Drive :class:`WebFetchTool` to re-fetch the page if needed."""
        res = await self._web_fetch.safe_run(
            {"url": url, "method": "GET", "timeout_seconds": timeout_seconds},
            ToolContext(requester_role="branding"),
        )
        data = res.data or {}
        return {
            "success": res.success,
            "error": res.error,
            "html": data.get("text", "") if isinstance(data, dict) else "",
            "final_url": str(data.get("final_url") or url) if isinstance(data, dict) else url,
        }

    async def run(self, params: BrandingParams, ctx: ToolContext) -> ToolResult:
        url = params.url

        # If dom_html is missing or empty, fetch it.
        html = params.dom_html or ""
        final_url = url
        if not html:
            try:
                fetched = await retry_async(
                    lambda: self._do_call(
                        url=url, timeout_seconds=params.timeout_seconds
                    ),
                    policy=self._retry_policy,
                    op_name="branding.fetch",
                    retriable=(Exception,),
                )
                if fetched.get("success"):
                    html = fetched.get("html", "") or ""
                    final_url = fetched.get("final_url", url)
            except Exception as exc:
                _log.warning(
                    "branding.fetch_failed",
                    extra={"tool": "branding", "url": url, "error": str(exc)},
                )

        meta = dict(params.page_metadata or {})
        if not meta and not html:
            return self._skip_payload(
                url=url,
                reason="no page_metadata and dom_html fetch failed",
            )

        snapshot = _extract_brand(meta, html)
        checks = _grade(snapshot)
        parsed = urlparse(final_url or url)

        return ToolResult.ok(
            "branding",
            data={
                "skipped": False,
                "reason": None,
                "url": url,
                "final_url": final_url,
                "scheme": parsed.scheme,
                "logo_url": snapshot["logo_url"],
                "favicons": snapshot["favicons"],
                "og_image": snapshot["og_image"],
                "twitter_image": snapshot["twitter_image"],
                "theme_color": snapshot["theme_color"],
                "brand_colors": snapshot["brand_colors"],
                "social_links": snapshot["social_links"],
                "title_variants": snapshot["title_variants"],
                "checks": checks,
                "checks_count": len(checks),
                "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            },
            url=url,
            checks_count=len(checks),
        )

    def _skip_payload(self, *, url: str, reason: str) -> ToolResult:
        return ToolResult.ok(
            "branding",
            data={
                "skipped": True,
                "reason": reason,
                "url": url,
                "final_url": url,
                "scheme": None,
                "logo_url": None,
                "favicons": [],
                "og_image": None,
                "twitter_image": None,
                "theme_color": None,
                "brand_colors": [],
                "social_links": [],
                "title_variants": {"page": None, "og": None, "twitter": None},
                "checks": [],
                "checks_count": 0,
                "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            },
            skipped=True,
            url=url,
        )


# ---------------------------------------------------------------------------
# Pure-function helpers (testable)
# ---------------------------------------------------------------------------


def _normalize_hex(color: str) -> str:
    """Normalize a hex color to the 6-char lowercase form for deduping."""
    c = color.strip().lower()
    if len(c) == 4:
        # #abc → #aabbcc
        return "#" + c[1] * 2 + c[2] * 2 + c[3] * 2
    if len(c) == 5:
        # #abcd → #aabbccdd
        return "#" + c[1] * 2 + c[2] * 2 + c[3] * 2 + c[4] * 2
    return c


def _extract_brand_colors(html: str) -> list[str]:
    """Best-effort brand color extraction from inline ``<style>`` blocks."""
    if not html:
        return []
    style_blocks = _STYLE_BLOCK_RX.findall(html)
    if not style_blocks:
        return []

    seen: set[str] = set()
    colors: list[str] = []
    for block in style_blocks:
        # First pass: CSS-variable color declarations (likely "brand" colors).
        for match in _CSSVAR_COLOR_RX.finditer(block):
            norm = _normalize_hex(match.group(1))
            if norm not in seen:
                seen.add(norm)
                colors.append(norm)
                if len(colors) >= _MAX_BRAND_COLORS:
                    return colors
        # Second pass: plain color/background declarations.
        for match in _COLOR_DECL_RX.finditer(block):
            norm = _normalize_hex(match.group(1))
            if norm not in seen:
                seen.add(norm)
                colors.append(norm)
                if len(colors) >= _MAX_BRAND_COLORS:
                    return colors
    return colors


def _extract_social_links(page_metadata: dict[str, Any], html: str) -> list[dict[str, str]]:
    """Collect social-platform links from page_metadata first, then html."""
    out: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def _add(url: str) -> None:
        if not isinstance(url, str) or not url:
            return
        if url in seen_urls:
            return
        if len(out) >= _MAX_SOCIAL_LINKS:
            return
        lowered = url.lower()
        for platform, host in _SOCIAL_HOSTS:
            if host in lowered:
                seen_urls.add(url)
                out.append({"platform": platform, "url": url})
                return

    # First: structured social_links from the crawler.
    for entry in page_metadata.get("social_links", []) or []:
        if isinstance(entry, dict):
            _add(entry.get("href", "") or entry.get("url", ""))
        elif isinstance(entry, str):
            _add(entry)
    # Then: parse raw <a href="..."> out of the HTML.
    if html and len(out) < _MAX_SOCIAL_LINKS:
        for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE):
            _add(match.group(1))
    return out


def _extract_title_variants(page_metadata: dict[str, Any]) -> dict[str, str | None]:
    metas = page_metadata.get("metas") or {}
    return {
        "page": page_metadata.get("title"),
        "og": metas.get("og:title"),
        "twitter": metas.get("twitter:title"),
    }


def _extract_brand(
    page_metadata: dict[str, Any], html: str
) -> dict[str, Any]:
    """Pull a brand snapshot from page_metadata + raw html."""
    metas = page_metadata.get("metas") or {}
    favicons = list(page_metadata.get("favicons") or [])[:_MAX_FAVICONS]

    logo_url = (
        metas.get("og:image")
        or metas.get("twitter:image")
        or (favicons[0].get("href") if favicons else None)
    )
    og_image = metas.get("og:image")
    twitter_image = metas.get("twitter:image")
    theme_color = metas.get("theme-color")

    social_links = _extract_social_links(page_metadata, html)
    brand_colors = _extract_brand_colors(html)
    title_variants = _extract_title_variants(page_metadata)

    return {
        "logo_url": logo_url,
        "favicons": favicons,
        "og_image": og_image,
        "twitter_image": twitter_image,
        "theme_color": theme_color,
        "brand_colors": brand_colors,
        "social_links": social_links,
        "title_variants": title_variants,
    }


def _grade(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic checks for the brand snapshot."""
    checks: list[dict[str, Any]] = []

    # --- Logo / OG image ---------------------------------------------
    has_logo = bool(snapshot.get("logo_url"))
    if not has_logo:
        checks.append(
            {
                "id": "no-logo",
                "severity": "major",
                "present": False,
                "value": None,
                "finding": (
                    "No logo, OG image, or favicon detected — brand identity "
                    "is missing in shared-link previews and bookmarks."
                ),
                "recommendation": (
                    "Add an `<meta property=\"og:image\">` (1200x630) and a "
                    "favicon.ico."
                ),
            }
        )

    # --- Theme color --------------------------------------------------
    if not snapshot.get("theme_color"):
        checks.append(
            {
                "id": "no-theme-color",
                "severity": "minor",
                "present": False,
                "value": None,
                "finding": (
                    "No `<meta name=\"theme-color\">` — mobile browser chrome "
                    "won't tint to the brand palette."
                ),
                "recommendation": "Add a theme-color meta with your primary brand hex.",
            }
        )

    # --- Social presence ---------------------------------------------
    social_count = len(snapshot.get("social_links") or [])
    if social_count < 2:
        checks.append(
            {
                "id": "low-social-presence",
                "severity": "minor",
                "present": social_count > 0,
                "value": social_count,
                "finding": (
                    f"Only {social_count} social link(s) detected — "
                    "audiences can't find the brand on the platforms they use."
                ),
                "recommendation": (
                    "Add links to your active social profiles (footer or "
                    "contact page)."
                ),
            }
        )

    # --- Title consistency --------------------------------------------
    variants = snapshot.get("title_variants") or {}
    page_title = (variants.get("page") or "").strip()
    og_title = (variants.get("og") or "").strip()
    tw_title = (variants.get("twitter") or "").strip()
    distinct_titles = {t for t in (page_title, og_title, tw_title) if t}
    if page_title and len(distinct_titles) > 1:
        checks.append(
            {
                "id": "title-inconsistent",
                "severity": "minor",
                "present": True,
                "value": list(distinct_titles),
                "finding": (
                    "Page title differs across og:title / twitter:title — "
                    "shared-link previews may show the wrong brand."
                ),
                "recommendation": (
                    "Align `<title>`, og:title, and twitter:title to a single "
                    "canonical value."
                ),
            }
        )

    # --- Brand colors (info-level signal) ---------------------------
    if snapshot.get("brand_colors"):
        checks.append(
            {
                "id": "brand-colors-detected",
                "severity": "info",
                "present": True,
                "value": snapshot["brand_colors"][:6],
                "finding": (
                    f"Detected {len(snapshot['brand_colors'])} brand color(s) "
                    "in inline CSS."
                ),
                "recommendation": (
                    "Document the palette in a brand guide and ensure OG "
                    "imagery uses the primary color."
                ),
            }
        )

    return checks


__all__ = [
    "BrandingParams",
    "BrandingTool",
    "_extract_brand",
    "_extract_brand_colors",
    "_extract_social_links",
    "_extract_title_variants",
    "_grade",
]
