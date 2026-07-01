"""`SecurityReviewerAgent` — HTTP security-headers reviewer.

Hybrid shape (mirrors :class:`PerformanceReviewerAgent`):

1. Call :class:`SecurityTool` to fetch the URL and grade the response
   headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options,
   Referrer-Policy, Permissions-Policy, secure cookies, HTTPS scheme,
   server banner).
2. **If the tool returned a skip payload, short-circuit**: emit a
   fully-shaped :class:`SecurityReport` with ``score=None`` and an
   ``info`` issue pointing at the unreachable target, never call
   the LLM.
3. Otherwise render a prompt that contains the deterministic check
   list and ask the LLM to score + turn the checks into severity-rated
   ``issues``.

Inherits from :class:`LLMAgent` so it reuses prompt rendering,
JSON-mode request, Pydantic validation, and the retry loop.

Severity mapping is fixed at the tool layer (critical/major/minor/info)
— the LLM confirms / refines wording in the editor pass; the rubric
stays consistent across reviewers.
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
from dhrubo.tools.security_tool import SecurityParams, SecurityTool
from dhrubo.tools.tool_interface import ToolContext

_log = get_logger("agents.security_reviewer")


class SecurityIssue(BaseModel):
    """One security issue."""

    severity: str = Field(pattern=r"^(critical|major|minor|info)$")
    title: str
    detail: str
    recommendation: str
    id: str | None = None


class SecurityReport(BaseModel):
    """Structured security sub-report."""

    score: int | None = Field(default=None, ge=0, le=100)
    summary: str = ""
    issues: list[SecurityIssue] = Field(default_factory=list)
    checks_count: int = 0
    headers_seen: list[str] = Field(default_factory=list)
    headers_missing: list[str] = Field(default_factory=list)
    scheme: str | None = None
    is_https: bool | None = None
    server_banner: str | None = None
    cookie_flags: list[dict[str, Any]] = Field(default_factory=list)
    final_url: str | None = None
    fetched_at: str | None = None
    skipped: bool = False


# Fully-shaped fallback returned when the security scan can't run.
_NO_SECURITY_DATA_REPORT = SecurityReport(
    score=None,
    summary="Security review skipped — the URL was unreachable.",
    issues=[
        SecurityIssue(
            severity="info",
            title="Security review not run",
            detail=(
                "The security tool did not run because the target URL was "
                "unreachable (DNS, transport, or HTTP error)."
            ),
            recommendation=(
                "Verify the URL is publicly reachable and re-run the audit "
                "to enable HTTP security-header analysis."
            ),
        )
    ],
    checks_count=0,
    headers_seen=[],
    headers_missing=[],
    scheme=None,
    is_https=None,
    server_banner=None,
    cookie_flags=[],
    final_url=None,
    fetched_at=None,
    skipped=True,
)


# Cap the size of the check JSON embedded in the prompt.
_MAX_CHECKS_BYTES = 8_000


class SecurityReviewerAgent(LLMAgent):
    role: ClassVar[str] = "security_reviewer"
    input_keys: ClassVar[tuple[str, ...]] = ("target_url", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("security_report",)
    required_tools: ClassVar[tuple[str, ...]] = ("security",)
    response_model: ClassVar[type[BaseModel]] = SecurityReport

    system_template: ClassVar[str] = (
        "You are a senior web-security reviewer. You are given a "
        "deterministic security-header checklist for a single URL: "
        "presence and value of CSP, HSTS, X-Frame-Options, "
        "X-Content-Type-Options, Referrer-Policy, Permissions-Policy, "
        "secure-cookie attributes, HTTPS scheme, and any server-banner "
        "leakage. Each check has an id, severity, finding, and "
        "recommendation. Produce a structured security audit. Focus on: "
        "missing critical headers (CSP, HSTS), insecure cookies, "
        "clickjacking protection, mixed-content risks, and "
        "version-leaking banners. Output ONLY a JSON object matching "
        "the provided schema; no prose."
    )

    user_template: ClassVar[str] = (
        "Target URL: {{ target_url }}\n"
        "Final URL: {{ final_url }}\n"
        "Title: {{ title }}\n"
        "Scheme: {{ scheme }}\n"
        "Is HTTPS: {{ is_https }}\n"
        "Server banner: {{ server_banner }}\n"
        "Headers seen: {{ headers_seen }}\n"
        "Headers missing: {{ headers_missing }}\n"
        "Cookie flags: {{ cookie_flags_lines }}\n\n"
        "Deterministic checks (id / severity / finding / recommendation):\n"
        "{{ checks_lines }}\n\n"
        "Trimmed check payload (JSON):\n----\n{{ checks_summary }}\n----\n\n"
        "Return a JSON object with: score (0-100 or null), summary (one "
        "sentence), issues (array of {severity, title, detail, recommendation}). "
        "Severity values: critical, major, minor, info. The issues list "
        "should reflect what the checklist actually contained; do not "
        "invent new checks."
    )

    def __init__(
        self,
        *,
        security_tool: SecurityTool | None = None,
        config_dir: Path | None = None,
    ) -> None:
        super().__init__(prompt_dir=None)
        self._tool: SecurityTool = security_tool or SecurityTool(config_dir=config_dir)

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        meta = ctx.inputs.get("page_metadata") or {}
        sec = (ctx.metadata or {}).get("_security_payload") or {}
        checks = sec.get("checks", []) or []
        checks_lines = _format_checks(checks)
        checks_summary = json.dumps(checks, ensure_ascii=False)[:_MAX_CHECKS_BYTES]
        cookie_flags = sec.get("cookie_flags", []) or []
        return {
            "target_url": meta.get("url", "") or ctx.inputs.get("target_url", ""),
            "final_url": meta.get("final_url", "") or sec.get("final_url", ""),
            "title": meta.get("title") or "(no title)",
            "scheme": sec.get("scheme") or "(unknown)",
            "is_https": "yes" if sec.get("is_https") else "no",
            "server_banner": sec.get("server_banner") or "(none)",
            "headers_seen": ", ".join(sec.get("headers_seen") or []) or "(none)",
            "headers_missing": ", ".join(sec.get("headers_missing") or []) or "(none)",
            "cookie_flags_lines": _format_cookies(cookie_flags),
            "checks_lines": checks_lines,
            "checks_summary": checks_summary,
        }

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""

    async def _fetch_security(self, ctx: AgentContext) -> dict[str, Any]:
        """Call the security tool and return its data dict (skip-payload or full)."""
        url = ctx.inputs.get("target_url") or (ctx.inputs.get("page_metadata") or {}).get("url")
        if not url:
            return {
                "skipped": True,
                "reason": "missing target_url",
                "url": None,
                "final_url": None,
                "status_code": None,
                "scheme": None,
                "is_https": None,
                "headers_seen": [],
                "headers_missing": [],
                "cookie_flags": [],
                "server_banner": None,
                "checks": [],
                "checks_count": 0,
                "fetched_at": None,
            }

        params = SecurityParams(url=str(url))
        tool_ctx = ToolContext(requester_role=self.role)
        result = await self._tool.safe_run(params.model_dump(), tool_ctx)
        if not result.success or result.data is None:
            _log.warning(
                "security.tool_failed",
                extra={"role": self.role, "error": result.error, "url": str(url)},
            )
            return {
                "skipped": True,
                "reason": result.error or "security tool failed",
                "url": str(url),
                "final_url": None,
                "status_code": None,
                "scheme": None,
                "is_https": None,
                "headers_seen": [],
                "headers_missing": [],
                "cookie_flags": [],
                "server_banner": None,
                "checks": [],
                "checks_count": 0,
                "fetched_at": None,
            }
        return dict(result.data or {})

    async def execute(self, ctx: AgentContext) -> AgentResult:
        sec = await self._fetch_security(ctx)
        if isinstance(ctx.metadata, dict):
            ctx.metadata["_security_payload"] = sec
        else:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                ctx.metadata = {"_security_payload": sec}

        if sec.get("skipped"):
            _log.info(
                "security.skipped",
                extra={"role": self.role, "reason": sec.get("reason")},
            )
            return AgentResult.ok(
                self.role,
                security_report=_NO_SECURITY_DATA_REPORT.model_dump(),
            )

        try:
            res = await super().execute(ctx)
        except Exception as exc:  # AgentError subclasses already structured
            return AgentResult.fail(self.role, error=str(exc))
        if not res.success:
            return res

        payload = res.outputs.get("response", {})
        payload["checks_count"] = sec.get("checks_count", 0)
        payload["headers_seen"] = list(sec.get("headers_seen") or [])
        payload["headers_missing"] = list(sec.get("headers_missing") or [])
        payload["scheme"] = sec.get("scheme")
        payload["is_https"] = sec.get("is_https")
        payload["server_banner"] = sec.get("server_banner")
        payload["cookie_flags"] = list(sec.get("cookie_flags") or [])
        payload["final_url"] = sec.get("final_url") or sec.get("url")
        payload["fetched_at"] = sec.get("fetched_at")
        payload["skipped"] = False
        return AgentResult.ok(self.role, security_report=payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_checks(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "(no security checks)"
    lines: list[str] = []
    for c in checks:
        lines.append(
            f"- [{c.get('severity', 'info').upper()}] {c.get('id', '?')}: "
            f"{c.get('finding', '')} → {c.get('recommendation', '')}"
        )
    return "\n".join(lines)


def _format_cookies(cookies: list[dict[str, Any]]) -> str:
    if not cookies:
        return "(no cookies)"
    lines: list[str] = []
    for c in cookies:
        flags = []
        if c.get("secure"):
            flags.append("Secure")
        if c.get("httponly"):
            flags.append("HttpOnly")
        if c.get("samesite"):
            flags.append(f"SameSite={c['samesite'].capitalize()}")
        lines.append(f"- `{c.get('name', '?')}` ({', '.join(flags) or 'no flags'})")
    return "\n".join(lines)


__all__ = [
    "SecurityIssue",
    "SecurityReport",
    "SecurityReviewerAgent",
]
