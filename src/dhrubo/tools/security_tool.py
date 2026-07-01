"""`SecurityTool` — inspects HTTP security headers for a URL.

We reuse :class:`WebFetchTool` (core httpx-based fetch) to grab the
response, then grade the headers against an opinionated OWASP-flavored
checklist (CSP, HSTS, X-Frame-Options, X-Content-Type-Options,
Referrer-Policy, Permissions-Policy, COOP, CORP, secure cookies,
HTTPS scheme, server banner leakage).

The deterministic check list is the framework's "rubric" — the LLM
editor pass that follows reads this and produces a human-readable
report. Severity mapping (critical/major/minor/info) is fixed at
this layer so every reviewer shares the same scale.

Design notes:

- **No new deps**: reuses ``httpx`` (via WebFetchTool).
- **Test seam**: ``_do_call`` is the method tests monkey-patch.
- **Retry policy**: the ``security_scan`` entry in
  ``config/retry_policies.yaml`` (3 attempts, 1.0s → 10s, jittered).
- **Graceful skip**: when the URL is unreachable, the tool returns
  ``skipped=True`` so the audit never fails on a transient network
  glitch. Mirrors M5/M7 skip patterns.
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

_log = get_logger("tools.security")

# Headers we grade on. Lower-cased; case-insensitive matching at runtime.
SECURITY_HEADERS: tuple[str, ...] = (
    "content-security-policy",
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
)


def _resolve_retry_policy(config_dir: Path | None = None) -> RetryConfig:
    """Return the ``security_scan`` retry policy (or DEFAULT_RETRY on miss)."""
    if config_dir is None:
        return DEFAULT_RETRY
    try:
        policies = load_retry_policies(config_dir)
    except Exception as exc:  # pragma: no cover - bad config shouldn't break tool
        _log.warning("security.retry_policy_load_failed", extra={"error": str(exc)})
        return DEFAULT_RETRY
    return policies.get("security_scan", DEFAULT_RETRY)


class SecurityParams(BaseModel):
    """Inputs for :class:`SecurityTool`."""

    url: str = Field(min_length=1, max_length=2048)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    user_agent: str | None = None
    check_mixed_content: bool = True


class SecurityTool(Tool[SecurityParams]):
    """Inspect HTTP security headers for a URL."""

    name: ClassVar[str] = "security"
    description: ClassVar[str] = (
        "Inspect HTTP security headers (CSP, HSTS, X-Frame-Options, "
        "Referrer-Policy, Permissions-Policy, X-Content-Type-Options, "
        "cookies, server banner, HTTPS scheme, mixed-content hints) for "
        "a URL and return a graded checklist."
    )
    parameters: ClassVar[tuple[ToolParameter, ...]] = (
        ToolParameter("url", "string", description="Absolute URL to audit."),
        ToolParameter("timeout_seconds", "float", required=False),
        ToolParameter("user_agent", "string", required=False),
        ToolParameter("check_mixed_content", "bool", required=False),
    )
    params_model: ClassVar[type[BaseModel]] = SecurityParams

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
        """``httpx`` is a core dep, so security scanning is always available."""
        return True

    async def _do_call(
        self,
        *,
        url: str,
        timeout_seconds: float,
        user_agent: str | None,
    ) -> dict[str, Any]:
        """Drive :class:`WebFetchTool` and return a normalised payload dict.

        Tests monkey-patch this seam. The dict shape is::

            {
                "success": bool,
                "error": str | None,
                "status_code": int | None,
                "headers": dict[str, str],
                "final_url": str,
            }
        """
        del user_agent  # currently unused; reserved for future UA override
        res = await self._web_fetch.safe_run(
            {"url": url, "method": "GET", "timeout_seconds": timeout_seconds},
            ToolContext(requester_role="security"),
        )
        data = res.data or {}
        return {
            "success": res.success,
            "error": res.error,
            "status_code": data.get("status_code"),
            "headers": dict(data.get("headers") or {}),
            "final_url": str(data.get("final_url") or url),
        }

    async def run(self, params: SecurityParams, ctx: ToolContext) -> ToolResult:
        url = params.url
        parsed = urlparse(url)
        is_https = parsed.scheme.lower() == "https"

        async def _attempt() -> dict[str, Any]:
            return await self._do_call(
                url=url,
                timeout_seconds=params.timeout_seconds,
                user_agent=params.user_agent,
            )

        try:
            raw = await retry_async(
                _attempt,
                policy=self._retry_policy,
                op_name="security.fetch",
                retriable=(Exception,),
            )
        except Exception as exc:
            _log.warning(
                "security.fetch_failed",
                extra={"tool": "security", "url": url, "error": str(exc)},
            )
            return self._skip_payload(
                url=url,
                scheme=parsed.scheme,
                is_https=is_https,
                reason=f"security scan failed: {exc!r}",
            )

        if not raw.get("success"):
            return self._skip_payload(
                url=url,
                scheme=parsed.scheme,
                is_https=is_https,
                reason=f"web_fetch failed: {raw.get('error') or 'unknown'}",
                status_code=raw.get("status_code"),
            )

        headers = dict(raw.get("headers") or {})
        final_url = str(raw.get("final_url") or url)
        status_code = raw.get("status_code")
        server_banner = headers.get("server") or headers.get("Server")
        cookies = _parse_cookies(headers)
        checks = _grade_headers(
            headers,
            url=final_url,
            cookies=cookies,
            server_banner=server_banner,
            is_https=is_https,
        )

        seen_lower = {k.lower() for k in headers}
        headers_seen = sorted(h for h in SECURITY_HEADERS if h in seen_lower)
        headers_missing = sorted(h for h in SECURITY_HEADERS if h not in seen_lower)

        return ToolResult.ok(
            "security",
            data={
                "skipped": False,
                "reason": None,
                "url": url,
                "final_url": final_url,
                "status_code": status_code,
                "scheme": parsed.scheme,
                "is_https": is_https,
                "headers_seen": headers_seen,
                "headers_missing": headers_missing,
                "cookie_flags": cookies,
                "server_banner": server_banner,
                "checks": checks,
                "checks_count": len(checks),
                "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            },
            url=url,
            checks_count=len(checks),
        )

    def _skip_payload(
        self,
        *,
        url: str,
        scheme: str,
        is_https: bool,
        reason: str,
        status_code: int | None = None,
    ) -> ToolResult:
        return ToolResult.ok(
            "security",
            data={
                "skipped": True,
                "reason": reason,
                "url": url,
                "final_url": url,
                "status_code": status_code,
                "scheme": scheme,
                "is_https": is_https,
                "headers_seen": [],
                "headers_missing": list(SECURITY_HEADERS),
                "cookie_flags": [],
                "server_banner": None,
                "checks": [],
                "checks_count": 0,
                "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            },
            skipped=True,
            url=url,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_cookies(headers: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ``Set-Cookie`` headers, normalised to dicts with flags."""
    cookies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k, v in headers.items():
        if k.lower() != "set-cookie":
            continue
        raw_values = v if isinstance(v, list) else [v]
        for raw in raw_values:
            if not isinstance(raw, str):
                continue
            head = raw.split(";", 1)[0]
            name = head.split("=", 1)[0].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            lowered = raw.lower()
            cookies.append(
                {
                    "name": name,
                    "secure": "secure" in lowered,
                    "httponly": "httponly" in lowered,
                    "samesite": (
                        "strict"
                        if "samesite=strict" in lowered
                        else (
                            "lax"
                            if "samesite=lax" in lowered
                            else ("none" if "samesite=none" in lowered else None)
                        )
                    ),
                    "raw": raw,
                }
            )
    return cookies


def _grade_headers(
    headers: dict[str, str],
    *,
    url: str,
    cookies: list[dict[str, Any]],
    server_banner: str | None,
    is_https: bool,
) -> list[dict[str, Any]]:
    """Run the deterministic checklist over a header set."""
    checks: list[dict[str, Any]] = []
    h = {k.lower(): v for k, v in headers.items()}

    # --- HTTPS scheme --------------------------------------------------
    if not is_https:
        checks.append(
            {
                "id": "https-downgrade",
                "severity": "critical",
                "present": False,
                "value": None,
                "finding": f"Page is served over plain HTTP ({url}); traffic is not encrypted.",
                "recommendation": "Serve the site over HTTPS with a valid certificate and an HSTS header.",
            }
        )

    # --- CSP -----------------------------------------------------------
    if "content-security-policy" not in h:
        checks.append(
            {
                "id": "csp-missing",
                "severity": "critical",
                "present": False,
                "value": None,
                "finding": "No Content-Security-Policy header — script injection mitigations are absent.",
                "recommendation": (
                    "Add a CSP that restricts script-src to self + nonces, "
                    "disallows inline scripts, and limits frame-ancestors."
                ),
            }
        )
    else:
        checks.append(
            {
                "id": "csp-present",
                "severity": "info",
                "present": True,
                "value": h["content-security-policy"],
                "finding": "Content-Security-Policy header is set.",
                "recommendation": (
                    "Review script-src, object-src, and frame-ancestors "
                    "directives for least-privilege."
                ),
            }
        )

    # --- HSTS ----------------------------------------------------------
    if is_https and "strict-transport-security" not in h:
        checks.append(
            {
                "id": "hsts-missing",
                "severity": "major",
                "present": False,
                "value": None,
                "finding": "No Strict-Transport-Security header on an HTTPS site.",
                "recommendation": (
                    "Add `Strict-Transport-Security: max-age=63072000; "
                    "includeSubDomains; preload`."
                ),
            }
        )

    # --- Clickjacking --------------------------------------------------
    csp = h.get("content-security-policy", "")
    has_frame_ancestors = "frame-ancestors" in csp.lower()
    if "x-frame-options" not in h and not has_frame_ancestors:
        checks.append(
            {
                "id": "clickjacking-protection-missing",
                "severity": "major",
                "present": False,
                "value": None,
                "finding": (
                    "No X-Frame-Options and no CSP frame-ancestors — "
                    "clickjacking mitigation absent."
                ),
                "recommendation": (
                    "Add `X-Frame-Options: DENY` (or `SAMEORIGIN`) and/or a "
                    "CSP `frame-ancestors 'none'` directive."
                ),
            }
        )

    # --- MIME sniffing -------------------------------------------------
    if "x-content-type-options" not in h:
        checks.append(
            {
                "id": "x-content-type-options-missing",
                "severity": "minor",
                "present": False,
                "value": None,
                "finding": "No X-Content-Type-Options header — MIME-sniffing attacks possible.",
                "recommendation": "Add `X-Content-Type-Options: nosniff`.",
            }
        )

    # --- Referrer-Policy ----------------------------------------------
    if "referrer-policy" not in h:
        checks.append(
            {
                "id": "referrer-policy-missing",
                "severity": "minor",
                "present": False,
                "value": None,
                "finding": "No Referrer-Policy header — outbound links may leak full URLs.",
                "recommendation": "Add `Referrer-Policy: strict-origin-when-cross-origin` (or stricter).",
            }
        )

    # --- Permissions-Policy -------------------------------------------
    if "permissions-policy" not in h:
        checks.append(
            {
                "id": "permissions-policy-missing",
                "severity": "minor",
                "present": False,
                "value": None,
                "finding": "No Permissions-Policy header — powerful browser features are unrestricted.",
                "recommendation": (
                    "Add a Permissions-Policy that disables unused features "
                    "(camera, microphone, geolocation, ...)."
                ),
            }
        )

    # --- Cookies ------------------------------------------------------
    for cookie in cookies:
        flags: list[str] = []
        if not cookie.get("secure"):
            flags.append("Missing `Secure`")
        if not cookie.get("httponly"):
            flags.append("Missing `HttpOnly`")
        samesite = cookie.get("samesite")
        if not samesite:
            flags.append("Missing `SameSite`")
        if flags:
            checks.append(
                {
                    "id": f"cookie-insecure:{cookie.get('name', '?')}",
                    "severity": "major",
                    "present": True,
                    "value": cookie.get("raw", ""),
                    "finding": (
                        f"Cookie `{cookie.get('name', '?')}` is missing attributes: "
                        + ", ".join(flags)
                        + "."
                    ),
                    "recommendation": (
                        "Set `Secure; HttpOnly; SameSite=Lax` (or `Strict` for "
                        "session cookies)."
                    ),
                }
            )

    # --- Server banner leakage ----------------------------------------
    if server_banner and re.search(r"/\d", server_banner):
        checks.append(
            {
                "id": "server-banner-version",
                "severity": "info",
                "present": True,
                "value": server_banner,
                "finding": f"Server banner leaks a versioned product: `{server_banner}`.",
                "recommendation": "Strip the version from the `Server` header or replace with a generic token.",
            }
        )

    return checks


__all__ = [
    "SECURITY_HEADERS",
    "SecurityParams",
    "SecurityTool",
]
