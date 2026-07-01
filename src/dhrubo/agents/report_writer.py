"""`ReportWriterAgent` — assembles sub-reports into a final Markdown audit report.

v1 is deterministic (no LLM). It composes a clean, structured Markdown
document. In a later milestone this may gain an LLM pass for narrative
polish, but the *facts* in the report will always come from the structured
sub-reports so they remain testable.

Single-page (M8 layout, default)::

    # Website Audit Report — <title>
    ## Page Snapshot
    ## Screenshots
    ## SEO Review
    ## UI Review
    ## Performance Review
    ## Accessibility Review
    ## Security Review
    ## Branding Review
    ## Methodology

Multi-page (M9, ``len(pages) >= 2``)::

    # Website Audit Report — <seed_domain>
    ## Summary
    ## Page 1 — <title>
        ### Page Snapshot
        ### Screenshots
        ### SEO Review
        ### UI Review
        ### Performance Review
        ### Accessibility Review
        ### Security Review
        ### Branding Review
    ## Page 2 — <title>
        … (same shape)
    ## Methodology

The single-page path keeps the M8 output byte-identical (every H2,
every table column, every badge). The multi-page path shares the
same per-section render helpers so the two layouts cannot drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def _severity_badge(severity: str) -> str:
    return {
        "critical": "🔴 Critical",
        "major": "🟠 Major",
        "minor": "🟡 Minor",
        "info": "🔵 Info",
    }.get(severity, severity)


# ---------------------------------------------------------------------------
# Per-section render helpers (pure functions over `lines`).
# ---------------------------------------------------------------------------


def _render_snapshot(lines: list[str], meta: dict[str, Any]) -> None:
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
        v_rendered = (
            "<br>".join(str(x) for x in v) or "(empty)"
            if isinstance(v, list)
            else str(v)
        )
        lines.append(f"| {k} | {v_rendered} |")
    lines.append("")


def _render_screenshots(lines: list[str], screenshots: list[dict[str, Any]]) -> None:
    if not screenshots:
        return
    for shot in screenshots:
        vp = shot.get("viewport", "?")
        path = shot.get("path", "")
        w = shot.get("width", 0)
        h = shot.get("height", 0)
        lines.append(f"- **{vp}** ({w}x{h}): `{path}`")
    lines.append("")


def _render_seo(lines: list[str], seo: dict[str, Any]) -> None:
    score = seo.get("score")
    summary = seo.get("summary", "")
    lines.append(f"**Score:** {score}/100  " if score is not None else "**Score:** n/a  ")
    if summary:
        lines.append(f"**Summary:** {summary}")
    lines.append("")
    issues = list(seo.get("issues") or [])
    issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
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


def _render_ui(
    lines: list[str], ui: dict[str, Any], screenshots: list[dict[str, Any]]
) -> None:
    ui_score = ui.get("score")
    if ui_score is None:
        lines.append("**Score:** n/a (UI review skipped)  ")
    else:
        lines.append(f"**Score:** {ui_score}/100  ")
    if ui.get("summary"):
        lines.append(f"**Summary:** {ui['summary']}")
    vp_seen = ui.get("viewports_seen") or [s.get("viewport", "?") for s in screenshots]
    if vp_seen:
        lines.append(f"_Viewports reviewed:_ {', '.join(vp_seen)}  ")
    lines.append("")
    ui_issues = list(ui.get("issues") or [])
    ui_issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
    if not ui_issues:
        lines.append("_No UI issues detected._")
    else:
        for issue in ui_issues:
            sev = issue.get("severity", "info")
            lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
            lines.append("")
            if issue.get("detail"):
                lines.append(f"- **Finding:** {issue['detail']}")
            if issue.get("recommendation"):
                lines.append(f"- **Recommendation:** {issue['recommendation']}")
            lines.append("")
    lines.append("")


def _render_perf(lines: list[str], perf: dict[str, Any]) -> None:
    perf_score = perf.get("score")
    if perf.get("skipped") or perf_score is None:
        lines.append("**Score:** n/a (Performance review skipped)  ")
    else:
        lines.append(f"**Score:** {perf_score}/100  ")
    if perf.get("summary"):
        lines.append(f"**Summary:** {perf['summary']}")
    if perf.get("strategy"):
        lines.append(f"_Strategy:_ {perf['strategy']}  ")
    lines.append(
        f"_CrUX field data available:_ {'yes' if perf.get('has_field_data') else 'no'}  "
    )
    lines.append("")

    metrics = list(perf.get("metrics") or [])
    if metrics:
        lines.append("**Core metrics:**")
        lines.append("")
        lines.append("| Metric | Value | Lighthouse score |")
        lines.append("|---|---|---|")
        for m in metrics:
            lines.append(
                f"| {m.get('title', m.get('id', '?'))} | "
                f"{m.get('display_value', '') or '—'} | "
                f"{m.get('score') if m.get('score') is not None else '—'} |"
            )
        lines.append("")

    opps = list(perf.get("opportunities") or [])
    if opps:
        lines.append("**Top optimization opportunities:**")
        lines.append("")
        for o in opps[:5]:
            savings = o.get("display_savings") or f"{o.get('savings_ms', 0)} ms"
            lines.append(f"- `{o.get('id', '?')}` — {o.get('title', '')} _(savings: {savings})_")
        lines.append("")

    perf_issues = list(perf.get("issues") or [])
    perf_issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
    if perf_issues:
        for issue in perf_issues:
            sev = issue.get("severity", "info")
            lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
            lines.append("")
            if issue.get("detail"):
                lines.append(f"- **Finding:** {issue['detail']}")
            if issue.get("recommendation"):
                lines.append(f"- **Recommendation:** {issue['recommendation']}")
            lines.append("")
    elif not (perf.get("skipped") or perf_score is None):
        lines.append("_No performance issues detected._")
        lines.append("")

    lines.append("")


def _render_a11y(lines: list[str], a11y: dict[str, Any]) -> None:
    a11y_score = a11y.get("score")
    if a11y.get("skipped") or a11y_score is None:
        lines.append("**Score:** n/a (Accessibility review skipped)  ")
    else:
        lines.append(f"**Score:** {a11y_score}/100  ")
    if a11y.get("summary"):
        lines.append(f"**Summary:** {a11y['summary']}")
    if a11y.get("viewport"):
        lines.append(f"_Viewport:_ {a11y['viewport']}  ")
    if a11y.get("tags_run"):
        lines.append(f"_WCAG tags run:_ {', '.join(a11y['tags_run'])}  ")
    lines.append(f"_Violations:_ {a11y.get('violations_count', 0)}  ")
    lines.append("")

    a11y_violations = list(a11y.get("violations") or [])
    if a11y_violations:
        lines.append("**Top axe-core violations:**")
        lines.append("")
        lines.append("| Rule | Impact | Severity | Nodes | Help |")
        lines.append("|---|---|---|---|---|")
        for v in a11y_violations[:10]:
            rule = v.get("id", "?")
            impact = v.get("impact", "?")
            sev = v.get("severity", "?")
            nodes = v.get("nodes_count", 0)
            help_text = (v.get("help", "") or "").replace("|", "\\|")
            lines.append(f"| `{rule}` | {impact} | {sev} | {nodes} | {help_text} |")
        lines.append("")

    a11y_issues = list(a11y.get("issues") or [])
    a11y_issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
    if a11y_issues:
        for issue in a11y_issues:
            sev = issue.get("severity", "info")
            lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
            lines.append("")
            if issue.get("detail"):
                lines.append(f"- **Finding:** {issue['detail']}")
            if issue.get("recommendation"):
                lines.append(f"- **Recommendation:** {issue['recommendation']}")
            lines.append("")
    elif not (a11y.get("skipped") or a11y_score is None):
        lines.append("_No accessibility issues detected._")
        lines.append("")

    lines.append("")


def _render_security(lines: list[str], sec: dict[str, Any]) -> None:
    sec_score = sec.get("score")
    if sec.get("skipped") or sec_score is None:
        lines.append("**Score:** n/a (Security review skipped)  ")
    else:
        lines.append(f"**Score:** {sec_score}/100  ")
    if sec.get("summary"):
        lines.append(f"**Summary:** {sec['summary']}")
    if sec.get("scheme"):
        lines.append(f"_Scheme:_ `{sec['scheme']}`  ")
    if sec.get("is_https") is not None:
        lines.append(f"_HTTPS:_ {'yes' if sec['is_https'] else 'no'}  ")
    headers_seen = sec.get("headers_seen") or []
    headers_missing = sec.get("headers_missing") or []
    if headers_seen or headers_missing:
        lines.append(
            f"_Headers seen:_ {len(headers_seen)}  ·  "
            f"_missing:_ {len(headers_missing)}  "
        )
    if sec.get("server_banner"):
        lines.append(f"_Server banner:_ `{sec['server_banner']}`  ")
    lines.append("")

    sec_checks = list(sec.get("checks") or sec.get("headers_seen") or [])
    if not sec_checks:
        sec_checks = [
            {"id": h, "severity": "minor", "present": False, "finding": f"`{h}` not present"}
            for h in headers_missing
        ]
    if sec_checks:
        lines.append("**Header checks:**")
        lines.append("")
        lines.append("| Header / Check | Present | Severity | Finding |")
        lines.append("|---|---|---|---|")
        for c in sec_checks[:10]:
            cid = c.get("id", "?")
            present = "yes" if c.get("present") else "no"
            sev = c.get("severity", "info")
            finding = (c.get("finding") or "").replace("|", "\\|")
            lines.append(f"| `{cid}` | {present} | {sev} | {finding} |")
        lines.append("")

    cookie_flags = list(sec.get("cookie_flags") or [])
    if cookie_flags:
        lines.append("**Cookie flags:**")
        lines.append("")
        lines.append("| Name | Secure | HttpOnly | SameSite |")
        lines.append("|---|---|---|---|")
        for c in cookie_flags[:10]:
            lines.append(
                f"| `{c.get('name', '?')}` | "
                f"{'yes' if c.get('secure') else 'no'} | "
                f"{'yes' if c.get('httponly') else 'no'} | "
                f"{c.get('samesite') or '—'} |"
            )
        lines.append("")

    sec_issues = list(sec.get("issues") or [])
    sec_issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
    if sec_issues:
        for issue in sec_issues:
            sev = issue.get("severity", "info")
            lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
            lines.append("")
            if issue.get("detail"):
                lines.append(f"- **Finding:** {issue['detail']}")
            if issue.get("recommendation"):
                lines.append(f"- **Recommendation:** {issue['recommendation']}")
            lines.append("")
    elif not (sec.get("skipped") or sec_score is None):
        lines.append("_No security issues detected._")
        lines.append("")

    lines.append("")


def _render_branding(lines: list[str], brand: dict[str, Any]) -> None:
    brand_score = brand.get("score")
    if brand.get("skipped") or brand_score is None:
        lines.append("**Score:** n/a (Branding review skipped)  ")
    else:
        lines.append(f"**Score:** {brand_score}/100  ")
    if brand.get("summary"):
        lines.append(f"**Summary:** {brand['summary']}")
    if brand.get("logo_url"):
        lines.append(f"_Logo URL:_ `{brand['logo_url']}`  ")
    if brand.get("og_image"):
        lines.append(f"_OG image:_ `{brand['og_image']}`  ")
    if brand.get("twitter_image"):
        lines.append(f"_Twitter image:_ `{brand['twitter_image']}`  ")
    if brand.get("theme_color"):
        lines.append(f"_Theme color:_ `{brand['theme_color']}`  ")
    brand_colors = list(brand.get("brand_colors") or [])
    if brand_colors:
        lines.append(f"_Brand colors:_ {', '.join(f'`{c}`' for c in brand_colors)}  ")
    lines.append("")

    favicons = list(brand.get("favicons") or [])
    if favicons:
        lines.append("**Favicons:**")
        lines.append("")
        lines.append("| URL | Sizes | Type |")
        lines.append("|---|---|---|")
        for f in favicons[:8]:
            href = (f.get("href") or "").replace("|", "\\|")
            lines.append(
                f"| `{href}` | {f.get('sizes', '') or '—'} | "
                f"{f.get('type', '') or '—'} |"
            )
        lines.append("")

    social_links = list(brand.get("social_links") or [])
    if social_links:
        lines.append("**Social links:**")
        lines.append("")
        for sl in social_links[:8]:
            lines.append(f"- `{sl.get('platform', '?')}`: {sl.get('url', '')}")
        lines.append("")

    title_variants = dict(brand.get("title_variants") or {})
    if any(title_variants.values()):
        lines.append("**Title variants:**")
        lines.append("")
        lines.append("| Source | Title |")
        lines.append("|---|---|")
        for src in ("page", "og", "twitter"):
            val = title_variants.get(src)
            if val:
                lines.append(f"| {src} | {val} |")
        lines.append("")

    brand_issues = list(brand.get("issues") or [])
    brand_issues.sort(
        key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 99), i.get("title", ""))
    )
    if brand_issues:
        for issue in brand_issues:
            sev = issue.get("severity", "info")
            lines.append(f"### {_severity_badge(sev)} — {issue.get('title', '')}")
            lines.append("")
            if issue.get("detail"):
                lines.append(f"- **Finding:** {issue['detail']}")
            if issue.get("recommendation"):
                lines.append(f"- **Recommendation:** {issue['recommendation']}")
            lines.append("")
    elif not (brand.get("skipped") or brand_score is None):
        lines.append("_No branding issues detected._")
        lines.append("")

    lines.append("")


# ---------------------------------------------------------------------------
# Page block (snapshot + screenshots + 6 sub-reviews).
# ---------------------------------------------------------------------------


def _render_page_block(
    lines: list[str],
    meta: dict[str, Any],
    screenshots: list[dict[str, Any]],
    sub_reports: dict[str, dict[str, Any]],
    *,
    headings: bool = False,
) -> None:
    """Render one page's worth of report: snapshot → screenshots → 6 reviews.

    ``sub_reports`` keys: ``seo``, ``ui``, ``perf``, ``a11y``, ``security``,
    ``branding``.

    When ``headings=True`` (multi-page path) each block is introduced by
    an H3 header — ``### Page Snapshot``, ``### Screenshots``,
    ``### SEO Review``, etc. — so the per-page sections are visually
    scannable under their parent ``## Page N — <title>`` H2.
    """
    if headings:
        lines.append("### Page Snapshot")
        lines.append("")
    _render_snapshot(lines, meta)
    if screenshots:
        if headings:
            lines.append("### Screenshots")
            lines.append("")
        else:
            lines.append("**Screenshots:**")
            lines.append("")
        _render_screenshots(lines, screenshots)
    if headings:
        lines.append("### SEO Review")
        lines.append("")
    _render_seo(lines, sub_reports.get("seo") or {})
    if headings:
        lines.append("### UI Review")
        lines.append("")
    _render_ui(lines, sub_reports.get("ui") or {}, screenshots)
    if headings:
        lines.append("### Performance Review")
        lines.append("")
    _render_perf(lines, sub_reports.get("perf") or {})
    if headings:
        lines.append("### Accessibility Review")
        lines.append("")
    _render_a11y(lines, sub_reports.get("a11y") or {})
    if headings:
        lines.append("### Security Review")
        lines.append("")
    _render_security(lines, sub_reports.get("security") or {})
    if headings:
        lines.append("### Branding Review")
        lines.append("")
    _render_branding(lines, sub_reports.get("branding") or {})


# ---------------------------------------------------------------------------
# Single-page top-level (M8 layout, preserved verbatim).
# ---------------------------------------------------------------------------


def _render_single_page_top(
    lines: list[str],
    target_url: str,
    page_title: str,
    meta: dict[str, Any],
    screenshots: list[dict[str, Any]],
    sub_reports: dict[str, dict[str, Any]],
) -> None:
    lines.append(f"# Website Audit Report — {page_title}")
    lines.append("")
    lines.append(f"_URL:_ `{target_url}`  ")
    lines.append(f"_Generated:_ {datetime.now(tz=UTC).isoformat(timespec='seconds')}  ")
    lines.append(f"_Final URL after redirects:_ `{meta.get('final_url', target_url)}`  ")
    lines.append(f"_HTTP status:_ {meta.get('status_code', 'n/a')}  ")
    lines.append("")
    lines.append("## Page Snapshot")
    lines.append("")
    _render_snapshot(lines, meta)
    if screenshots:
        lines.append("## Screenshots")
        lines.append("")
        _render_screenshots(lines, screenshots)
    # M8 uses H2 for the six reviews.
    lines.append("## SEO Review")
    lines.append("")
    _render_seo(lines, sub_reports.get("seo") or {})
    lines.append("## UI Review")
    lines.append("")
    _render_ui(lines, sub_reports.get("ui") or {}, screenshots)
    lines.append("## Performance Review")
    lines.append("")
    _render_perf(lines, sub_reports.get("perf") or {})
    lines.append("## Accessibility Review")
    lines.append("")
    _render_a11y(lines, sub_reports.get("a11y") or {})
    lines.append("## Security Review")
    lines.append("")
    _render_security(lines, sub_reports.get("security") or {})
    lines.append("## Branding Review")
    lines.append("")
    _render_branding(lines, sub_reports.get("branding") or {})


# ---------------------------------------------------------------------------
# Multi-page summary + per-page blocks (M9 layout).
# ---------------------------------------------------------------------------


def _score_of(payload: dict[str, Any]) -> int | None:
    s = payload.get("score")
    return s if isinstance(s, int) else None


def _render_summary(
    lines: list[str],
    pages: list[dict[str, Any]],
    page_payloads: dict[str, dict[str, Any]],
) -> None:
    lines.append("**Pages audited:** " + str(len(pages)))
    lines.append("")
    lines.append("| # | URL | Title |")
    lines.append("|---|---|---|")
    for i, page in enumerate(pages):
        payload = page_payloads.get(str(i), {})
        title = payload.get("title") or page.get("url", "?")
        lines.append(f"| {i + 1} | `{page.get('url', '?')}` | {title} |")
    lines.append("")

    # Best/worst per lens.
    lenses = (
        ("SEO", "seo"),
        ("UI", "ui"),
        ("Performance", "perf"),
        ("Accessibility", "a11y"),
        ("Security", "security"),
        ("Branding", "branding"),
    )
    lines.append("**Lens scores per page:**")
    lines.append("")
    header = "| Page | " + " | ".join(lens[0] for lens in lenses) + " |"
    sep = "|---|" + "|".join("---" for _ in lenses) + "|"
    lines.append(header)
    lines.append(sep)
    for i, _page in enumerate(pages):
        payload = page_payloads.get(str(i), {})
        row = [f"{i + 1}"]
        for _lname, key in lenses:
            score = _score_of(payload.get(key) or {})
            row.append(str(score) if score is not None else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")


def _render_multi_page(
    lines: list[str],
    pages: list[dict[str, Any]],
    seed_domain: str,
    page_payloads: dict[str, dict[str, Any]],
) -> None:
    """Render the multi-page report body (everything except H1 + Methodology)."""
    lines.append(f"# Website Audit Report — {seed_domain}")
    lines.append("")
    lines.append(f"_Pages audited:_ {len(pages)}  ")
    lines.append(f"_Generated:_ {datetime.now(tz=UTC).isoformat(timespec='seconds')}  ")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    _render_summary(lines, pages, page_payloads)

    for i, page in enumerate(pages):
        payload = page_payloads.get(str(i), {})
        meta = payload.get("page_metadata") or {}
        screenshots = payload.get("screenshots") or []
        title = meta.get("title") or page.get("url", f"Page {i + 1}")
        lines.append(f"## Page {i + 1} — {title}")
        lines.append("")
        _render_page_block(
            lines,
            meta,
            screenshots,
            {
                "seo": payload.get("seo_report") or {},
                "ui": payload.get("ui_report") or {},
                "perf": payload.get("performance_report") or {},
                "a11y": payload.get("a11y_report") or {},
                "security": payload.get("security_report") or {},
                "branding": payload.get("branding_report") or {},
            },
            headings=True,
        )


# ---------------------------------------------------------------------------
# M10 — diff section (single- + multi-page aware).
# ---------------------------------------------------------------------------


_LENS_TITLES = {
    "seo_report": "SEO",
    "ui_report": "UI",
    "performance_report": "Performance",
    "a11y_report": "Accessibility",
    "security_report": "Security",
    "branding_report": "Branding",
}


def _lens_title(lens_key: str) -> str:
    return _LENS_TITLES.get(lens_key, lens_key.replace("_report", "").title())


def render_diff_section(
    lines: list[str],
    diff_payload: dict[str, Any],
    previous_run_id: str,
    *,
    multi_page: bool,
) -> None:
    """Render ``## Diff vs <previous_run_id>`` at the top of the report.

    Layout:

    - One H2 (``## Diff vs <id>``) with the summary line.
    - For each change kind (added, removed, severity_changed,
      score_changed): an H3 section.
    - For multi-page: each row carries a ``page`` key; rows are
      grouped under ``### Page <N> — <title>`` sub-blocks.
    - For single-page: rows are flat (no page grouping).
    """
    lines.append(f"## Diff vs `{previous_run_id}`")
    lines.append("")
    summary = diff_payload.get("summary", "")
    if summary:
        lines.append(f"_{summary}_")
        lines.append("")

    # Bucket changes per page for multi-page. For single-page, page=None.
    page_labels: dict[str, str] = {}
    for kind in ("added", "removed", "severity_changed", "score_changed"):
        rows = diff_payload.get(kind) or []
        if not rows:
            continue
        lines.append(f"### {_kind_title(kind)}")
        lines.append("")
        if multi_page:
            # Group by page.
            by_page: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                page = str(r.get("page") or "")
                by_page.setdefault(page, []).append(r)
            for page in sorted(by_page):
                page_rows = by_page[page]
                title = page_labels.get(page) or f"Page {int(page) + 1}"
                lines.append(f"**{title}**")
                lines.append("")
                _render_diff_rows(lines, page_rows, kind)
        else:
            _render_diff_rows(lines, rows, kind)
        lines.append("")


def _kind_title(kind: str) -> str:
    return {
        "added": "Added issues",
        "removed": "Removed issues",
        "severity_changed": "Severity changes",
        "score_changed": "Score changes",
    }.get(kind, kind)


def _render_diff_rows(
    lines: list[str], rows: list[dict[str, Any]], kind: str
) -> None:
    if kind == "score_changed":
        lines.append("| Lens | Score (was → now) | Δ |")
        lines.append("|---|---|---|")
        for r in rows:
            lens = _lens_title(str(r.get("lens", "?")))
            sa = r.get("score_a")
            sb = r.get("score_b")
            delta = r.get("delta", 0)
            sign = "+" if isinstance(delta, int) and delta > 0 else ""
            lines.append(f"| {lens} | {sa} → {sb} | {sign}{delta} |")
        return

    # added/removed: full issue dicts; severity_changed: lens+id+sev.
    if kind == "severity_changed":
        lines.append("| Lens | Title | Severity (was → now) |")
        lines.append("|---|---|---|")
        for r in rows:
            lens = _lens_title(str(r.get("lens", "?")))
            title = str(r.get("title", "(no title)")).replace("|", "\\|")
            lines.append(
                f"| {lens} | {title} | "
                f"{r.get('severity_a', '?')} → {r.get('severity_b', '?')} |"
            )
        return

    # added / removed — issue dict with id + severity + title.
    lines.append("| Lens | Severity | Title | ID |")
    lines.append("|---|---|---|---|")
    for r in rows:
        issue = r.get("issue") or {}
        lens = _lens_title(str(r.get("lens", "?")))
        sev = str(issue.get("severity", "?"))
        title = str(issue.get("title", "(no title)")).replace("|", "\\|")
        iid = str(issue.get("id", "—"))
        lines.append(f"| {lens} | {sev} | {title} | `{iid}` |")


# ---------------------------------------------------------------------------
# Agent.
# ---------------------------------------------------------------------------


class ReportWriterAgent(BaseAgent):
    role: ClassVar[str] = "report_writer"
    # Single-page: the original M8 keys. Multi-page: also reads
    # ``pages``, ``seed_domain``, and namespaced per-page keys. The
    # engine only enforces the keys listed here as *required* (it
    # actually doesn't enforce at all — this classvar is doc-only).
    input_keys: ClassVar[tuple[str, ...]] = (
        "pages",
        "seed_domain",
        "seo_report",
        "ui_report",
        "performance_report",
        "a11y_report",
        "security_report",
        "branding_report",
        "page_metadata",
        "screenshot_paths",
    )
    output_keys: ClassVar[tuple[str, ...]] = ("final_report_md",)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        pages: list[dict[str, Any]] = list(ctx.inputs.get("pages") or [])
        seed_domain: str = ctx.inputs.get("seed_domain") or ""

        lines: list[str] = []
        current_sub_reports: dict[str, Any] = {}
        multi_page = len(pages) >= 2

        if multi_page:
            # ---- Multi-page path ----
            page_payloads = _collect_multi_page_payloads(ctx, len(pages))
            _render_multi_page(lines, pages, seed_domain, page_payloads)
            sections = ["summary", "screenshots"] + [f"page_{i + 1}" for i in range(len(pages))]
            sub_reports = _multi_sub_report_keys(len(pages))
            pages_urls = [p.get("url", "") for p in pages]
            # Plumb the per-page structured payloads for the exporter.
            for i, payload in page_payloads.items():
                current_sub_reports[str(i)] = payload
        else:
            # ---- Single-page path (M8 verbatim) ----
            seo = ctx.inputs.get("seo_report") or {}
            ui = ctx.inputs.get("ui_report") or {}
            perf = ctx.inputs.get("performance_report") or {}
            a11y = ctx.inputs.get("a11y_report") or {}
            sec = ctx.inputs.get("security_report") or {}
            brand = ctx.inputs.get("branding_report") or {}
            meta = ctx.inputs.get("page_metadata") or {}
            screenshots = ctx.inputs.get("screenshot_paths") or []
            target_url = meta.get("url", "unknown")
            page_title = meta.get("title", "(no title)")
            _render_single_page_top(
                lines,
                target_url,
                page_title,
                meta,
                screenshots,
                {
                    "seo": seo,
                    "ui": ui,
                    "perf": perf,
                    "a11y": a11y,
                    "security": sec,
                    "branding": brand,
                },
            )
            sections = [
                "seo",
                "ui",
                "performance",
                "accessibility",
                "security",
                "branding",
                "screenshots",
            ]
            sub_reports = [
                "seo_report",
                "ui_report",
                "performance_report",
                "a11y_report",
                "security_report",
                "branding_report",
            ]
            pages_urls = [target_url]
            # Plumb the single-page structured payloads for the exporter.
            current_sub_reports = {
                "seo_report": seo,
                "ui_report": ui,
                "performance_report": perf,
                "a11y_report": a11y,
                "security_report": sec,
                "branding_report": brand,
            }

        # Methodology (both paths share this).
        lines.append("## Methodology")
        lines.append("")
        if multi_page:
            lines.append(
                f"This v0.6 multi-page audit covers {len(pages)} pages across "
                "SEO, UI (vision-based), Performance (PageSpeed Insights), "
                "Accessibility (axe-core), Security (HTTP header grading), "
                "and Branding (logo, favicon, social presence, inline CSS "
                "palette) reviews. All findings are produced by structured "
                "LLM sub-agents validated against Pydantic schemas."
            )
        else:
            lines.append(
                "This v0.5 audit covers SEO, UI (vision-based), Performance "
                "(PageSpeed Insights), Accessibility (axe-core), Security "
                "(HTTP header grading), and Branding (logo, favicon, social "
                "presence, inline CSS palette) reviews. All findings are "
                "produced by structured LLM sub-agents validated against "
                "Pydantic schemas."
            )
        lines.append("")

        md = "\n".join(lines).rstrip() + "\n"

        metadata: dict[str, Any] = {
            "sections": sections,
            "sub_reports": sub_reports,
            "sub_reports_payload": current_sub_reports,
        }
        if multi_page:
            metadata["pages"] = pages_urls
            metadata["seed_domain"] = seed_domain

        # Note: the ``## Diff vs <run_id>`` section is rendered by
        # the exporter (M11 fix) — not here. The DAG shape
        # ``report → diff → export`` means the diff isn't computed
        # when this task runs. The exporter reads both
        # ``final_report_md`` and ``diff_payload`` and prepends the
        # section before writing report.md.

        return AgentResult.ok(
            self.role,
            final_report_md=md,
            report_metadata=metadata,
            # M10: structured sub-reports for the diff task + the
            # exporter. Same shape as ``report_metadata["sub_reports_payload"]``
            # but promoted to a top-level output key so the engine's
            # SessionMemory writes it under ``ctx.inputs["sub_reports"]``
            # for downstream tasks.
            sub_reports=current_sub_reports,
        )


def _collect_multi_page_payloads(
    ctx: AgentContext, n: int
) -> dict[str, dict[str, Any]]:
    """Pull each per-page sub-report dict out of ``ctx.inputs`` by namespaced key.

    Returns a mapping ``{"0": {"seo_report": ..., "page_metadata": ..., ...}, ...}``.
    Missing payloads become empty dicts; missing per-page sub-reports
    render as empty (the per-section renderers fall back to "n/a").
    """
    out: dict[str, dict[str, Any]] = {}
    keys_map = {
        "seo_report": "seo_report",
        "ui_report": "ui_report",
        "performance_report": "performance_report",
        "a11y_report": "a11y_report",
        "security_report": "security_report",
        "branding_report": "branding_report",
        "page_metadata": "page_metadata",
        "screenshots": "screenshots",
    }
    for i in range(n):
        per_page: dict[str, Any] = {}
        for short, namespaced in (
            ("seo_report", f"page_{i}_seo_report"),
            ("ui_report", f"page_{i}_ui_report"),
            ("performance_report", f"page_{i}_performance_report"),
            ("a11y_report", f"page_{i}_a11y_report"),
            ("security_report", f"page_{i}_security_report"),
            ("branding_report", f"page_{i}_branding_report"),
            ("page_metadata", f"page_{i}_page_metadata"),
            ("screenshots", f"page_{i}_screenshot_paths"),
        ):
            per_page[keys_map[short]] = ctx.inputs.get(namespaced) or {}
        out[str(i)] = per_page
    return out


def _multi_sub_report_keys(n: int) -> list[str]:
    """Build the report_metadata["sub_reports"] list for multi-page."""
    out: list[str] = []
    for i in range(n):
        for base in (
            "seo_report",
            "ui_report",
            "performance_report",
            "a11y_report",
            "security_report",
            "branding_report",
        ):
            out.append(f"page_{i}_{base}")
    return out
