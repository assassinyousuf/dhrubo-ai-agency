"""`LighthouseTool` — wraps the PageSpeed Insights v5 API.

PSI runs a Lighthouse audit server-side and returns the same JSON that the
``lighthouse`` CLI produces locally. We talk to it over HTTPS with
``httpx`` (already a core dependency) — no Node/Chrome binary required.

Design notes:

- **No API key → skip-with-info payload**, never an exception. The audit
  pipeline degrades gracefully; the report renders ``n/a (Performance
  review skipped)``. Same UX as the UI reviewer when screenshots are
  missing.
- **Retry policy** is loaded from :mod:`dhrubo.config.loader` (the
  ``pagespeed_call`` entry in ``config/retry_policies.yaml``), falling
  back to :data:`dhrubo.core.retry.DEFAULT_RETRY`.
- **HTTP-level mocking seam**: ``_do_call`` is an instance method that
  callers can monkey-patch in tests. Production code calls
  ``httpx.AsyncClient.get`` directly.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field

from dhrubo.config.loader import load_retry_policies
from dhrubo.core.errors import ToolError
from dhrubo.core.logger import get_logger
from dhrubo.core.retry import DEFAULT_RETRY, RetryConfig, retry_async
from dhrubo.tools.tool_interface import Tool, ToolContext, ToolParameter, ToolResult

_log = get_logger("tools.lighthouse")

_PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Audits we surface as structured metrics in the tool output.
_METRIC_AUDITS: tuple[str, ...] = (
    "first-contentful-paint",
    "largest-contentful-paint",
    "total-blocking-time",
    "cumulative-layout-shift",
    "speed-index",
    "interactive",
    "first-meaningful-paint",
)

# A pre-shaped payload returned when no API key is configured. The agent
# recognises this and short-circuits its LLM call.
_SKIP_PAYLOAD: dict[str, Any] = {
    "skipped": True,
    "reason": "PAGESPEED_API_KEY (or GOOGLE_API_KEY) is not set",
    "score": None,
    "strategy": None,
    "final_url": None,
    "fetched_at": None,
    "has_field_data": False,
    "metrics": [],
    "opportunities": [],
    "raw": None,
}


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``pagespeed_call`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("lighthouse.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("pagespeed_call", DEFAULT_RETRY)


class LighthouseParams(BaseModel):
    """Inputs for :class:`LighthouseTool`."""

    url: str = Field(min_length=1, max_length=2048)
    strategy: Literal["mobile", "desktop"] = "mobile"
    categories: list[str] = Field(default_factory=lambda: ["performance"])
    locale: str = Field(default="en", min_length=2, max_length=8)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=180.0)


class LighthouseTool(Tool[LighthouseParams]):
    """Call PageSpeed Insights v5 and return a structured performance summary."""

    name: ClassVar[str] = "lighthouse"
    description: ClassVar[str] = (
        "Run a PageSpeed Insights audit for a URL and return score, "
        "core-web-vitals metrics, top opportunities, and CrUX field data "
        "when available."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Absolute URL to audit."),
        ToolParameter("strategy", "mobile|desktop", required=False),
        ToolParameter("categories", "string[]", required=False),
        ToolParameter("locale", "string", required=False),
        ToolParameter("timeout_seconds", "float", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = LighthouseParams

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._retry_policy: RetryConfig = _resolve_retry_policy(config_dir)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def has_api_key() -> bool:
        """Return True if ``PAGESPEED_API_KEY`` or ``GOOGLE_API_KEY`` is set."""
        return bool(
            os.environ.get("PAGESPEED_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )

    # ------------------------------------------------------------------
    # Hooks for tests / future local Lighthouse integration
    # ------------------------------------------------------------------

    async def _do_call(
        self,
        *,
        url: str,
        params: dict[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        """Make a single PSI call. Override or monkeypatch in tests."""
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            return await client.get(url, params=params)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    async def run(self, params: LighthouseParams, ctx: ToolContext) -> ToolResult:
        if not self.has_api_key():
            _log.info(
                "lighthouse.skipped_no_api_key",
                extra={"tool": self.name, "url": params.url, "requester": ctx.requester_role},
            )
            return ToolResult.ok(
                self.name,
                data=dict(_SKIP_PAYLOAD),
                skipped=True,
                url=params.url,
            )

        api_key = os.environ.get("PAGESPEED_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        query: dict[str, Any] = {
            "url": params.url,
            "key": api_key,
            "strategy": params.strategy,
            "locale": params.locale,
        }
        for cat in params.categories:
            query["category"] = cat  # PSI accepts repeated keys; last wins, fine for v1

        _log.info(
            "lighthouse.start",
            extra={
                "tool": self.name,
                "url": params.url,
                "strategy": params.strategy,
                "requester": ctx.requester_role,
            },
        )

        try:
            response = await retry_async(
                lambda: self._do_call(
                    url=_PSI_ENDPOINT,
                    params=query,
                    timeout_seconds=params.timeout_seconds,
                ),
                policy=self._retry_policy,
                op_name="lighthouse.runPagespeed",
                retriable=(httpx.HTTPError,),
            )
        except httpx.HTTPError as exc:
            raise ToolError(
                f"PageSpeed Insights transport error: {exc!r}",
                context={"tool": self.name, "url": params.url, "requester": ctx.requester_role},
                cause=exc,
            ) from exc

        if response.status_code >= 400:
            raise ToolError(
                f"PageSpeed Insights returned HTTP {response.status_code}",
                context={
                    "tool": self.name,
                    "url": params.url,
                    "status_code": response.status_code,
                    "requester": ctx.requester_role,
                },
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolError(
                "PageSpeed Insights returned non-JSON body",
                context={"tool": self.name, "url": params.url},
                cause=exc,
            ) from exc

        summary = _summarize(payload, strategy=params.strategy, final_url=payload.get("id"))
        return ToolResult.ok(
            self.name,
            data=summary,
            skipped=False,
            url=params.url,
            strategy=params.strategy,
        )


# ---------------------------------------------------------------------------
# PSI payload → tool-output mapping
# ---------------------------------------------------------------------------


def _summarize(
    payload: dict[str, Any],
    *,
    strategy: str,
    final_url: str | None,
) -> dict[str, Any]:
    """Extract the bits we care about from a full PSI v5 payload."""
    lhr = payload.get("lighthouseResult") or {}
    categories = lhr.get("categories") or {}
    perf_cat = categories.get("performance") or {}
    raw_score = perf_cat.get("score")
    score: int | None = None
    if isinstance(raw_score, (int, float)):
        score = round(raw_score * 100)

    audits = lhr.get("audits") or {}
    metrics: list[dict[str, Any]] = []
    for audit_id in _METRIC_AUDITS:
        a = audits.get(audit_id)
        if not isinstance(a, dict):
            continue
        metrics.append(
            {
                "id": audit_id,
                "title": a.get("title", audit_id),
                "value": a.get("numericValue"),
                "display_value": a.get("displayValue", ""),
                "score": a.get("score"),
            }
        )

    opportunities: list[dict[str, Any]] = []
    for a in audits.values():
        if not isinstance(a, dict):
            continue
        if a.get("details", {}).get("type") != "opportunity":
            continue
        savings = a.get("details", {}).get("overallSavingsMs")
        if not isinstance(savings, (int, float)) or savings <= 0:
            continue
        opportunities.append(
            {
                "id": a.get("id", ""),
                "title": a.get("title", ""),
                "savings_ms": int(savings),
                "display_savings": a.get("displayValue", ""),
                "score": a.get("score"),
            }
        )
    opportunities.sort(key=lambda o: o.get("savings_ms", 0), reverse=True)

    loading_exp = payload.get("loadingExperience") or {}
    metrics_block = loading_exp.get("metrics") if isinstance(loading_exp, dict) else None
    has_field_data = bool(metrics_block)

    return {
        "skipped": False,
        "reason": None,
        "score": score,
        "strategy": strategy,
        "final_url": final_url,
        "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "has_field_data": has_field_data,
        "metrics": metrics,
        "opportunities": opportunities,
        # Trim the raw payload to a known set of keys to avoid huge prompts.
        "raw": {
            "finalUrl": lhr.get("finalUrl"),
            "fetchTime": lhr.get("fetchTime"),
            "userAgent": lhr.get("userAgent"),
        },
    }


__all__ = ["LighthouseParams", "LighthouseTool"]
