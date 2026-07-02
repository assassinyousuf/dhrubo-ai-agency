"""Technology detection tool.

Scans headers, script src attributes, and meta tags to detect common CMS, 
analytics, and frameworks (similar to a lightweight Wappalyzer).
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, Field

from dhrubo.core.logger import get_logger
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.technology_detector")

# Lightweight regex patterns for common tech stack signatures
_TECH_SIGNATURES = {
    "WordPress": {
        "headers": {"x-powered-by": r"WP", "link": r"api\.w\.org"},
        "html": [r"wp-content/themes", r"wp-includes", r"<meta name=\"generator\" content=\"WordPress"]
    },
    "Shopify": {
        "headers": {"x-shopid": r".*", "x-shopify-stage": r".*"},
        "html": [r"cdn\.shopify\.com", r"window\.Shopify"]
    },
    "Wix": {
        "headers": {"x-wix-request-id": r".*"},
        "html": [r"wix\.com", r"wix-viewer-app"]
    },
    "React": {
        "headers": {},
        "html": [r"data-reactroot", r"id=\"root\""]
    },
    "Next.js": {
        "headers": {},
        "html": [r"__NEXT_DATA__", r"/_next/static"]
    },
    "Google Analytics": {
        "headers": {},
        "html": [r"google-analytics\.com/analytics\.js", r"gtag/js", r"ga\('create'"]
    },
    "Google Tag Manager": {
        "headers": {},
        "html": [r"googletagmanager\.com/gtm\.js"]
    },
    "Facebook Pixel": {
        "headers": {},
        "html": [r"connect\.facebook\.net/en_US/fbevents\.js", r"fbq\('init'"]
    },
    "Cloudflare": {
        "headers": {"server": r"cloudflare", "cf-ray": r".*"},
        "html": []
    },
    "jQuery": {
        "headers": {},
        "html": [r"jquery.*\.js"]
    },
    "Tailwind CSS": {
        "headers": {},
        "html": [r"tailwind", r"tw-"]
    },
    "Bootstrap": {
        "headers": {},
        "html": [r"bootstrap.*\.css", r"bootstrap.*\.js"]
    }
}

class TechDetectorParams(BaseModel):
    """Inputs for Technology Detector."""
    url: str = Field(min_length=1, max_length=2048)
    html: str = Field(default="")
    headers: dict[str, str] = Field(default_factory=dict)

class TechnologyDetectorTool(Tool[TechDetectorParams]):
    """Analyzes HTML and headers to detect tech stack."""

    name: ClassVar[str] = "technology_detector"
    description: ClassVar[str] = "Detect frameworks, CMS, and analytics used by the site."
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="URL of the site"),
        ToolParameter("html", "string", description="Raw HTML of the page"),
        ToolParameter("headers", "dict", description="HTTP response headers"),
    )
    params_model: ClassVar[type[BaseModel]] = TechDetectorParams

    async def run(self, params: TechDetectorParams, ctx: ToolContext) -> ToolResult:
        _log.info(f"Detecting technologies for {params.url}")

        detected = []
        html_lower = params.html.lower()
        headers_lower = {k.lower(): str(v).lower() for k, v in params.headers.items()}

        for tech, sigs in _TECH_SIGNATURES.items():
            found = False
            # Check headers
            for h_key, h_regex in sigs.get("headers", {}).items():
                if h_key in headers_lower:
                    if re.search(h_regex, headers_lower[h_key], re.IGNORECASE):
                        detected.append(tech)
                        found = True
                        break

            if found:
                continue

            # Check HTML
            for html_regex in sigs.get("html", []):
                if re.search(html_regex, html_lower, re.IGNORECASE):
                    detected.append(tech)
                    break

        return ToolResult.ok(
            self.name,
            data={"technologies": detected},
            technologies=detected
        )
