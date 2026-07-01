"""`markdown_diff_renderer` — pure-function Markdown renderer for diff payloads (M12).

Consumes the dict produced by :func:`dhrubo.tools.diff_tool._diff`
(or :func:`compute_diff`) and emits a single Markdown body
suitable for posting to a GitHub PR comment.

Layout:

- ``## Website Audit Diff`` H2 header.
- Sub-line: ``Comparing <run_id_a> -> <run_id_b>``.
- One-line summary (``X added, Y removed, ...``).
- Per-lens markdown table (lens / +N / -M / Δscore).
- For each lens with changes, a ``<details>`` block listing
  the added and removed issues (severity + title + id).
- A ``truncated`` marker when the per-lens cap is hit.

Designed to render *any* diff payload: works for single-page
(no ``page`` keys) and multi-page (rows carry ``page`` keys).
The renderer ignores the ``page`` key — the table aggregates
per lens; the details blocks do the same. (PR-comment readers
typically don't care about per-page breakdown; that's what
``diff.json`` on disk is for.)

Used by:
- ``dhrubo publish`` CLI subcommand (posts the body to a PR).
- ``PublisherAgent`` (programmatic equivalent).
"""

from __future__ import annotations

import contextlib
from typing import Any

# Lens display order + human-readable titles. Matches the
# report writer's table ordering.
_LENS_ORDER: tuple[str, ...] = (
    "seo_report",
    "ui_report",
    "performance_report",
    "a11y_report",
    "security_report",
    "branding_report",
)
_LENS_TITLES: dict[str, str] = {
    "seo_report": "SEO",
    "ui_report": "UI",
    "performance_report": "Performance",
    "a11y_report": "Accessibility",
    "security_report": "Security",
    "branding_report": "Branding",
}

_SEVERITY_RANK: dict[str, int] = {"critical": 0, "major": 1, "minor": 2, "info": 3}
_DEFAULT_MAX = 50


def render_diff_comment(
    diff_payload: dict[str, Any],
    *,
    max_issues_per_lens: int = _DEFAULT_MAX,
) -> str:
    """Render a diff payload as a single Markdown body.

    >>> body = render_diff_comment({
    ...     "run_id_a": "a", "run_id_b": "b",
    ...     "added": [{"lens": "seo_report", "page": None,
    ...               "issue": {"id": "x:1", "severity": "major",
    ...                         "title": "T", "detail": "d",
    ...                         "recommendation": "r"}}],
    ...     "removed": [], "severity_changed": [], "score_changed": [],
    ...     "summary": "1 added, 0 removed, 0 severity-changed, 0 score-changed",
    ... })
    >>> "## Website Audit Diff" in body
    True
    >>> "Comparing `a` -> `b`" in body
    True
    """
    if max_issues_per_lens < 0:
        max_issues_per_lens = 0

    lines: list[str] = []
    lines.append("## Website Audit Diff")
    lines.append("")
    lines.append(
        f"Comparing `{diff_payload.get('run_id_a', '?')}` -> "
        f"`{diff_payload.get('run_id_b', '?')}`"
    )
    lines.append("")
    summary = str(diff_payload.get("summary", ""))
    if summary:
        lines.append(f"_{summary}_")
        lines.append("")

    added = list(diff_payload.get("added") or [])
    removed = list(diff_payload.get("removed") or [])
    score_changed = list(diff_payload.get("score_changed") or [])
    severity_changed = list(diff_payload.get("severity_changed") or [])

    per_lens_added = _group_by_lens(added, "issue")
    per_lens_removed = _group_by_lens(removed, "issue")
    per_lens_score = _group_by_lens(score_changed, None)
    per_lens_severity = _group_by_lens(severity_changed, None)

    has_any = any(
        per_lens_added[lens] or per_lens_removed[lens] or per_lens_score[lens] or per_lens_severity[lens]
        for lens in _LENS_ORDER
    )
    if has_any:
        lines.append("| Lens | Added | Removed | Score Δ |")
        lines.append("|---|---:|---:|---:|")
        for lens in _LENS_ORDER:
            n_added = len(per_lens_added[lens])
            n_removed = len(per_lens_removed[lens])
            score_delta = _sum_score_delta(per_lens_score[lens])
            score_cell = (
                f"{'+' if score_delta >= 0 else ''}{score_delta}"
                if score_delta
                else "—"
            )
            lines.append(
                f"| {_LENS_TITLES[lens]} | {n_added} | {n_removed} | {score_cell} |"
            )
        lines.append("")

    # Per-lens details (added + removed + severity-changed).
    any_details = False
    for lens in _LENS_ORDER:
        block = _render_lens_details(
            lens,
            per_lens_added[lens],
            per_lens_removed[lens],
            per_lens_severity[lens],
            max_issues_per_lens=max_issues_per_lens,
        )
        if block:
            lines.extend(block)
            any_details = True

    if not has_any and not any_details:
        lines.append("_No structural changes._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _group_by_lens(
    rows: list[dict[str, Any]],
    issue_key: str | None,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {lens: [] for lens in _LENS_ORDER}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lens = str(row.get("lens", ""))
        if lens in out:
            out[lens].append(row)
    for lens in out:
        out[lens].sort(
            key=lambda r: (
                _SEVERITY_RANK.get(
                    str(
                        (r.get("issue") or {}).get("severity")
                        if issue_key
                        else r.get("severity_b")
                    ),
                    99,
                ),
                str(r.get("id") or (r.get("issue") or {}).get("id") or ""),
            )
        )
    return out


def _sum_score_delta(rows: list[dict[str, Any]]) -> int:
    total = 0
    for r in rows:
        delta = r.get("delta")
        with contextlib.suppress(TypeError, ValueError):
            total += int(delta or 0)
    return total


def _render_lens_details(
    lens: str,
    added: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    severity_changed: list[dict[str, Any]],
    *,
    max_issues_per_lens: int,
) -> list[str]:
    """Render one lens's ``<details>`` block. Returns ``[]`` when
    the lens has no rows."""
    if not added and not removed and not severity_changed:
        return []
    lines: list[str] = []
    title = _LENS_TITLES[lens]
    total = len(added) + len(removed) + len(severity_changed)
    lines.append(f"<details><summary>{title} ({total} change{'s' if total != 1 else ''})</summary>")
    lines.append("")

    if added:
        lines.append(f"**Added ({len(added)})**")
        lines.append("")
        for row in added[:max_issues_per_lens] if max_issues_per_lens else []:
            issue = row.get("issue") or {}
            lines.append(
                f"- `{_md_severity(issue.get('severity'))}` "
                f"**{_md_escape(str(issue.get('title', '?')))}** "
                f"(`{_md_escape(str(issue.get('id', '?')))}`)"
            )
        if max_issues_per_lens and len(added) > max_issues_per_lens:
            lines.append(
                f"- _…and {len(added) - max_issues_per_lens} more added (truncated; "
                "see `diff.json` for the full list)._"
            )
        lines.append("")

    if removed:
        lines.append(f"**Removed ({len(removed)})**")
        lines.append("")
        for row in removed[:max_issues_per_lens] if max_issues_per_lens else []:
            issue = row.get("issue") or {}
            lines.append(
                f"- `{_md_severity(issue.get('severity'))}` "
                f"**{_md_escape(str(issue.get('title', '?')))}** "
                f"(`{_md_escape(str(issue.get('id', '?')))}`)"
            )
        if max_issues_per_lens and len(removed) > max_issues_per_lens:
            lines.append(
                f"- _…and {len(removed) - max_issues_per_lens} more removed (truncated)._"
            )
        lines.append("")

    if severity_changed:
        lines.append(f"**Severity changed ({len(severity_changed)})**")
        lines.append("")
        for row in severity_changed[:max_issues_per_lens] if max_issues_per_lens else []:
            lines.append(
                f"- `{_md_escape(str(row.get('severity_a', '?')))}` -> "
                f"`{_md_escape(str(row.get('severity_b', '?')))}` "
                f"**{_md_escape(str(row.get('title', '?')))}** "
                f"(`{_md_escape(str(row.get('id', '?')))}`)"
            )
        if max_issues_per_lens and len(severity_changed) > max_issues_per_lens:
            lines.append(
                f"- _…and {len(severity_changed) - max_issues_per_lens} more severity changes (truncated)._"
            )
        lines.append("")

    lines.append("</details>")
    lines.append("")
    return lines


def _md_severity(value: object) -> str:
    """Render a severity string as a short emoji-style badge. Plain
    text on purpose — GitHub strips styling in issue comments
    unless you use a colored badge via shields.io, which we
    avoid to keep the comment self-contained."""
    s = str(value or "info")
    return s


def _md_escape(value: str) -> str:
    """Escape a string for safe inclusion in inline-code / bold spans."""
    return value.replace("`", "\\`").replace("\n", " ")


__all__ = ["render_diff_comment"]
