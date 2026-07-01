"""`ReportWriterAgent` — assembles sub-reports into a final Markdown audit report.

v1 is deterministic (no LLM). It composes a clean, structured Markdown
document. In a later milestone this may gain an LLM pass for narrative
polish, but the *facts* in the report will always come from the structured
sub-reports so they remain testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def _severity_badge(severity: str) -> str:
    return {
        "critical": "🔴 Critical",
        "major": "🟠 Major",
        "minor": "🟡 Minor",
        "info": "🔵 Info",
    }.get(severity, severity)


class ReportWriterAgent(BaseAgent):
    role: ClassVar[str] = "report_writer"
    input_keys: ClassVar[tuple[str, ...]] = ("seo_report", "page_metadata")
    output_keys: ClassVar[tuple[str, ...]] = ("final_report_md",)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        seo = ctx.inputs.get("seo_report") or {}
        meta = ctx.inputs.get("page_metadata") or {}
        screenshots = ctx.inputs.get("screenshot_paths") or []
        target_url = meta.get("url", "unknown")
        page_title = meta.get("title", "(no title)")

        lines: list[str] = []
        lines.append(f"# Website Audit Report — {page_title}")
        lines.append("")
        lines.append(f"_URL:_ `{target_url}`  ")
        lines.append(f"_Generated:_ {datetime.now(tz=UTC).isoformat(timespec='seconds')}  ")
        lines.append(f"_Final URL after redirects:_ `{meta.get('final_url', target_url)}`  ")
        lines.append(f"_HTTP status:_ {meta.get('status_code', 'n/a')}  ")
        lines.append("")
        lines.append("## Page Snapshot")
        lines.append("")
        snapshot = {
            "Title": meta.get("title") or "(no <title>)",
            "H1s": meta.get("h1s", []),
            "Meta description": (meta.get("metas") or {}).get("description", "(none)"),
            "Links": meta.get("links_count", 0),
            "Images": meta.get("images_count", 0),
            "Images without alt": meta.get("images_without_alt", 0),
            "Word count": meta.get("word_count", 0),
            "Render mode": meta.get("render_mode", "http"),
        }
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        for k, v in snapshot.items():
            if isinstance(v, list):
                v_rendered = "<br>".join(str(x) for x in v) or "(empty)"
            else:
                v_rendered = str(v)
            lines.append(f"| {k} | {v_rendered} |")
        lines.append("")

        if screenshots:
            lines.append("## Screenshots")
            lines.append("")
            for shot in screenshots:
                vp = shot.get("viewport", "?")
                path = shot.get("path", "")
                w = shot.get("width", 0)
                h = shot.get("height", 0)
                lines.append(f"- **{vp}** ({w}x{h}): `{path}`")
            lines.append("")

        # SEO section
        lines.append("## SEO Review")
        lines.append("")
        score = seo.get("score")
        summary = seo.get("summary", "")
        lines.append(f"**Score:** {score}/100  " if score is not None else "**Score:** n/a  ")
        if summary:
            lines.append(f"**Summary:** {summary}")
        lines.append("")
        issues = list(seo.get("issues") or [])
        issues.sort(key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", "")))
        if not issues:
            lines.append("_No SEO issues detected._")
        else:
            for issue in issues:
                sev = issue.get("severity", "info")
                lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
                lines.append("")
                if issue.get("detail"):
                    lines.append(f"- **Finding:** {issue['detail']}")
                if issue.get("recommendation"):
                    lines.append(f"- **Recommendation:** {issue['recommendation']}")
                lines.append("")
        lines.append("")

        lines.append("## Methodology")
        lines.append("")
        lines.append(
            "This v0.1 audit covers SEO only. Additional reviewers (UI, performance, "
            "accessibility, security, branding) land in subsequent milestones. "
            "All findings are produced by structured LLM sub-agents validated "
            "against Pydantic schemas."
        )
        lines.append("")

        md = "\n".join(lines).rstrip() + "\n"
        return AgentResult.ok(
            self.role,
            final_report_md=md,
            report_metadata={"sections": ["seo", "screenshots"], "sub_reports": ["seo_report"]},
        )
