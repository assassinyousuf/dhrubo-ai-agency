"""`BrandingReviewerAgent` — brand identity reviewer.

Hybrid shape (mirrors :class:`SecurityReviewerAgent`):

1. Call :class:`BrandingTool` to extract brand identity signals from
   the page metadata + inline CSS (logo, favicon, OG/Twitter image,
   theme color, brand colors, social links, title variants).
2. **If the tool returned a skip payload, short-circuit**: emit a
   fully-shaped :class:`BrandingReport` with ``score=None`` and an
   ``info`` issue pointing at the missing data, never call the LLM.
3. Otherwise render a prompt that contains the extracted snapshot
   and the deterministic check list, and ask the LLM to score + turn
   the checks into severity-rated ``issues``.

Inherits from :class:`LLMAgent` so it reuses prompt rendering,
JSON-mode request, Pydantic validation, and the retry loop.

The deterministic checks are presence-only signals (logo present?
social links > 2?); the LLM editor pass does the real grading and
explains brand consistency in plain English.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from dhrubo.agents.base_agent import AgentContext, AgentResult
from dhrubo.agents.llm_agent import LLMAgent
from dhrubo.core.logger import get_logger
from dhrubo.tools.branding_tool import BrandingParams, BrandingTool
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.branding_reviewer")


class BrandingIssue(BaseModel):
    """One branding issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class BrandingReport(BaseModel):
    """Structured branding sub-report."""

    score: int | None = Field(default=None, ge=0, le=100)
    summary: str = ""
    issues: list[BrandingIssue] = Field(default_factory=list)
    checks_count: int = 0
    logo_url: str | None = None
    favicons: list[dict[str, Any]] = Field(default_factory=list)
    og_image: str | None = None
    twitter_image: str | None = None
    theme_color: str | None = None
    brand_colors: list[str] = Field(default_factory=list)
    social_links: list[dict[str, str]] = Field(default_factory=list)
    title_variants: dict[str, str | None] = Field(default_factory=dict)
    final_url: str | None = None
    fetched_at: str | None = None
    skipped: bool = False


# Fully-shaped fallback returned when branding can't run.
_NO_BRANDING_DATA_REPORT = BrandingReport(
    score=None,
    summary="Branding review skipped — no page metadata or HTML was available.",
    issues=[
        BrandingIssue(
            severity="info",
            title="Branding review not run",
            detail=(
                "The branding tool did not run because neither page_metadata "
                "nor the page HTML could be retrieved."
            ),
            recommendation=(
                "Verify the URL is publicly reachable and the crawler "
                "succeeded; re-run to enable brand-identity analysis."
            ),
        )
    ],
    checks_count=0,
    logo_url=None,
    favicons=[],
    og_image=None,
    twitter_image=None,
    theme_color=None,
    brand_colors=[],
    social_links=[],
    title_variants={"page": None, "og": None, "twitter": None},
    final_url=None,
    fetched_at=None,
    skipped=True,
)


# Cap the size of the snapshot embedded in the prompt.
_MAX_SNAPSHOT_BYTES = 8_000


class BrandingReviewerAgent(LLMAgent):
    role: ClassVar[str] = "branding_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url", "page_metadata", "dom_html")
    output_keys: ClassVar[tuple[str, ...]] = ("branding_report",)
    required_tools: ClassVar[tuple[str, ...]] = ("branding",)
    response_model: ClassVar[type[BaseModel]] = BrandingReport

    system_template: ClassVar[str] = (
        "You are a senior brand-identity reviewer. You are given a "
        "deterministic brand snapshot for a single URL: logo URL, "
        "favicons, OG/Twitter image, theme color, extracted brand "
        "colors from inline CSS, social-link presence, and title "
        "variants. Each check has an id, severity, finding, and "
        "recommendation. Produce a structured brand-identity audit. "
        "Focus on: presence of a logo / OG image, theme-color usage, "
        "social presence, brand-color consistency, and title alignment. "
        "Output ONLY a JSON object matching the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Logo URL: {{ logo_url }}\n"
        "OG image: {{ og_image }}\n"
        "Twitter image: {{ twitter_image }}\n"
        "Theme color: {{ theme_color }}\n"
        "Brand colors (inline CSS): {{ brand_colors }}\n"
        "Social links: {{ social_links_lines }}\n"
        "Title variants: {{ title_variants_lines }}\n\n"
        "Deterministic checks (id / severity / finding / recommendation):\n"
        "{{ checks_lines }}\n\n"
        "Trimmed snapshot (JSON):\n----\n{{ snapshot_summary }}\n----\n\n"
        "Return a JSON object with: score (0-100 or null), summary (one "
        "sentence), issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info. The issues list "
        "should reflect what the snapshot actually contained; do not "
        "invent new checks."
    )

    def __init__(
        self,
        *,
        branding_tool: BrandingTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        super().__init__(prompt_dir=None)
        self._tool: BrandingTool = branding_tool or BrandingTool(config_dir=config_dir)

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        brand = (ctx.metadata or {}).get("_branding_payload") or {}
        checks = brand.get("checks", []) or []
        checks_lines = _format_checks(checks)
        snapshot = {
            "logo_url": brand.get("logo_url"),
            "favicons": brand.get("favicons", []),
            "og_image": brand.get("og_image"),
            "twitter_image": brand.get("twitter_image"),
            "theme_color": brand.get("theme_color"),
            "brand_colors": brand.get("brand_colors", []),
            "social_links": brand.get("social_links", []),
            "title_variants": brand.get("title_variants", {}),
        }
        snapshot_summary = json.dumps(snapshot, ensure_ascii=False)[:_MAX_SNAPSHOT_BYTES]
        title_variants = brand.get("title_variants") or {}
        return {
            "target_url": meta.get("url", "") or ctx.inputs.get("target_url", ""),
            "final_url": meta.get("final_url", "") or brand.get("final_url", ""),
            "title": meta.get("title") or "(no title)",
            "logo_url": brand.get("logo_url") or "(none)",
            "og_image": brand.get("og_image") or "(none)",
            "twitter_image": brand.get("twitter_image") or "(none)",
            "theme_color": brand.get("theme_color") or "(none)",
            "brand_colors": ", ".join(brand.get("brand_colors") or []) or "(none)",
            "social_links_lines": _format_social_links(brand.get("social_links", []) or []),
            "title_variants_lines": _format_title_variants(title_variants),
            "checks_lines": checks_lines,
            "snapshot_summary": snapshot_summary,
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def _fetch_branding(self, ctx: AgentContext) -> dict[str, Any]:
        """Call the branding tool and return its data dict (skip-payload or full)."""
        url = ctx.inputs.get("target_url") or (ctx.inputs.get("page_metadata") or {}).get("url")
        if not url:
            return {
                "skipped": True,
                "reason": "missing target_url",
                "url": None,
                "final_url": None,
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
                "fetched_at": None,
            }

        params = BrandingParams(
            url=str(url),
            page_metadata=dict(ctx.inputs.get("page_metadata") or {}),
            dom_html=ctx.inputs.get("dom_html"),
        )
        tool_ctx = ToolContext(requester_role=self.role)
        result = await self._tool.safe_run(params.model_dump(), tool_ctx)
        if not result.success or result.data is None:
            _log.warning(
                "branding.tool_failed",
                extra={"role": self.role, "error": result.error, "url": str(url)},
            )
            return {
                "skipped": True,
                "reason": result.error or "branding tool failed",
                "url": str(url),
                "final_url": None,
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
                "fetched_at": None,
            }
        return dict(result.data or {})

    async def execute(self, ctx: AgentContext) -> AgentResult:
        brand = await self._fetch_branding(ctx)
        if isinstance(ctx.metadata, dict):
            ctx.metadata["_branding_payload"] = brand
        else:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                ctx.metadata = {"_branding_payload": brand}

        if brand.get("skipped"):
            _log.info(
                "branding.skipped",
                extra={"role": self.role, "reason": brand.get("reason")},
            )
            return AgentResult.ok(
                self.role,
                branding_report=_NO_BRANDING_DATA_REPORT.model_dump(),
            )

        try:
            res = await super().execute(ctx)
        except Exception as exc:
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res

        payload = res.outputs.get("response", {})
        payload["checks_count"] = brand.get("checks_count", 0)
        payload["logo_url"] = brand.get("logo_url")
        payload["favicons"] = list(brand.get("favicons") or [])
        payload["og_image"] = brand.get("og_image")
        payload["twitter_image"] = brand.get("twitter_image")
        payload["theme_color"] = brand.get("theme_color")
        payload["brand_colors"] = list(brand.get("brand_colors") or [])
        payload["social_links"] = list(brand.get("social_links") or [])
        payload["title_variants"] = dict(brand.get("title_variants") or {})
        payload["final_url"] = brand.get("final_url") or brand.get("url")
        payload["fetched_at"] = brand.get("fetched_at")
        payload["skipped"] = False
        return AgentResult.ok(self.role, branding_report=payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_checks(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "(no branding checks)"
    lines: list[str] = []
    for c in checks:
        lines.append(
            f"- [{c.get('severity', 'info').upper()}] {c.get('id', '?')}: "
            f"{c.get('finding', '')} → {c.get('recommendation', '')}"
        )
    return "\n".join(lines)


def _format_social_links(links: list[dict[str, str]]) -> str:
    if not links:
        return "(no social links)"
    return "\n".join(f"- `{link.get('platform', '?')}`: {link.get('url', '')}" for link in links)


def _format_title_variants(variants: dict[str, str | None]) -> str:
    if not variants:
        return "(no title variants)"
    return ", ".join(
        f"{k}={v!r}" for k, v in variants.items() if v
    ) or "(all empty)"


__all__ = [
    "BrandingIssue",
    "BrandingReport",
    "BrandingReviewerAgent",
]
