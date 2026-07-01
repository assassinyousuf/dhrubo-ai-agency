"""Tests for :mod:`dhrubo.agents.report_writer`."""

from __future__ import annotations

from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.report_writer import ReportWriterAgent


def _agent() -> ReportWriterAgent:
    return ReportWriterAgent()


# ---------------------------------------------------------------------------
# Single-page (M8 layout, byte-stable)
# ---------------------------------------------------------------------------


async def test_single_page_layout_preserved() -> None:
    """The single-page path must keep the M8 H2 layout verbatim."""
    agent = _agent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://example.com/",
            "page_metadata": {
                "url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "title": "Example Domain",
                "h1s": ["Example Domain"],
                "metas": {"description": "Example desc"},
                "links_count": 1,
                "images_count": 0,
                "images_without_alt": 0,
                "word_count": 100,
                "render_mode": "http",
            },
            "seo_report": {
                "score": 80,
                "summary": "ok",
                "issues": [
                    {
                        "severity": "minor",
                        "title": "Title short",
                        "detail": "…",
                        "recommendation": "…",
                    }
                ],
            },
            "ui_report": {"score": 75, "summary": "ok", "issues": []},
            "performance_report": {"score": 60, "summary": "ok", "issues": []},
            "a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "security_report": {"score": 70, "summary": "ok", "issues": []},
            "branding_report": {"score": 65, "summary": "ok", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    # Single-page H2 sections.
    for section in (
        "## Page Snapshot",
        "## SEO Review",
        "## UI Review",
        "## Performance Review",
        "## Accessibility Review",
        "## Security Review",
        "## Branding Review",
        "## Methodology",
    ):
        assert section in md, f"missing {section}"
    # No multi-page artefacts.
    assert "## Summary" not in md
    assert "## Page 1" not in md
    # Methodology is the v0.5 single-page blurb.
    assert "v0.5" in md
    # report_metadata has the M8 sections / sub_reports lists.
    meta = res.metadata.get("report_metadata", res.outputs.get("report_metadata", {}))
    assert "seo" in meta["sections"]
    assert "seo_report" in meta["sub_reports"]


# ---------------------------------------------------------------------------
# Multi-page
# ---------------------------------------------------------------------------


async def test_multi_page_renders_summary_and_per_page_sections() -> None:
    agent = _agent()
    pages = [
        {"index": 0, "url": "https://a/", "slug": "https_a_"},
        {"index": 1, "url": "https://b/", "slug": "https_b_"},
    ]
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "pages": pages,
            "seed_domain": "a",
            # Per-page sub-reports (namespaced).
            "page_0_page_metadata": {
                "url": "https://a/",
                "final_url": "https://a/",
                "status_code": 200,
                "title": "Homepage A",
            },
            "page_0_seo_report": {"score": 80, "summary": "ok", "issues": []},
            "page_0_ui_report": {"score": 70, "summary": "ok", "issues": []},
            "page_0_performance_report": {"score": 60, "summary": "ok", "issues": []},
            "page_0_a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "page_0_security_report": {"score": 70, "summary": "ok", "issues": []},
            "page_0_branding_report": {"score": 65, "summary": "ok", "issues": []},
            "page_1_page_metadata": {
                "url": "https://b/",
                "final_url": "https://b/",
                "status_code": 200,
                "title": "Page B",
            },
            "page_1_seo_report": {"score": 50, "summary": "weak", "issues": []},
            "page_1_ui_report": {"score": 40, "summary": "weak", "issues": []},
            "page_1_performance_report": {"score": 30, "summary": "weak", "issues": []},
            "page_1_a11y_report": {"score": 20, "summary": "weak", "issues": []},
            "page_1_security_report": {"score": 10, "summary": "weak", "issues": []},
            "page_1_branding_report": {"score": 5, "summary": "weak", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    # Multi-page sections.
    assert "## Summary" in md
    assert "## Page 1 — Homepage A" in md
    assert "## Page 2 — Page B" in md
    # Per-page sub-reviews appear inside each page block.
    for n in (1, 2):
        for lens in ("SEO Review", "UI Review", "Performance Review",
                     "Accessibility Review", "Security Review", "Branding Review"):
            # `###` because per-page sections nest under `## Page N — …`.
            assert f"### {lens}" in md, f"missing ### {lens} in page {n}"
    # Methodology is the v0.6 multi-page blurb.
    assert "v0.6" in md
    assert "2 pages" in md
    # Summary scores table includes both pages' scores.
    # The summary table renders one row per page with all six lens scores.
    # Verify the per-lens best/worst on each row is present.
    assert "| 1 |" in md
    assert "| 2 |" in md
    # Report metadata has the M9 keys.
    meta = res.metadata.get("report_metadata", res.outputs.get("report_metadata", {}))
    assert "summary" in meta["sections"]
    assert meta["pages"] == ["https://a/", "https://b/"]
    assert meta["seed_domain"] == "a"
    assert "page_0_seo_report" in meta["sub_reports"]


async def test_multi_page_handles_missing_per_page_report() -> None:
    """A missing per-page sub-report renders as a skipped section (no crash)."""
    agent = _agent()
    pages = [
        {"index": 0, "url": "https://a/", "slug": "https_a_"},
        {"index": 1, "url": "https://b/", "slug": "https_b_"},
    ]
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "pages": pages,
            "seed_domain": "a",
            # Only page 0 has a real sub-report; page 1 is empty.
            "page_0_page_metadata": {
                "url": "https://a/",
                "final_url": "https://a/",
                "status_code": 200,
                "title": "Page A",
            },
            "page_0_seo_report": {"score": 80, "summary": "ok", "issues": []},
            "page_0_ui_report": {"score": 70, "summary": "ok", "issues": []},
            "page_0_performance_report": {"score": 60, "summary": "ok", "issues": []},
            "page_0_a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "page_0_security_report": {"score": 70, "summary": "ok", "issues": []},
            "page_0_branding_report": {"score": 65, "summary": "ok", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    assert res.success is True
    md = res.outputs["final_report_md"]
    # Both pages are present.
    assert "## Page 1 — Page A" in md
    assert "## Page 2 — https://b/" in md
    # Page 2's lens sections show "n/a (skipped)".
    # The score is None for missing payloads → "n/a" appears.
    assert "n/a" in md


async def test_single_page_with_pages_input_uses_single_layout() -> None:
    """If ``pages`` has length 1 the writer still uses the M8 single-page layout."""
    agent = _agent()
    pages = [{"index": 0, "url": "https://a/", "slug": "https_a_"}]
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "pages": pages,
            "seed_domain": "a",
            "page_metadata": {
                "url": "https://a/",
                "final_url": "https://a/",
                "status_code": 200,
                "title": "Single A",
            },
            "seo_report": {"score": 80, "summary": "ok", "issues": []},
            "ui_report": {"score": 70, "summary": "ok", "issues": []},
            "performance_report": {"score": 60, "summary": "ok", "issues": []},
            "a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "security_report": {"score": 70, "summary": "ok", "issues": []},
            "branding_report": {"score": 65, "summary": "ok", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    # Single-page H2 layout (not ## Page 1).
    assert "## SEO Review" in md
    assert "## Summary" not in md
    assert "## Page 1" not in md


# ---------------------------------------------------------------------------
# M10 — comparison / diff section
# ---------------------------------------------------------------------------


async def test_single_page_with_diff_renders_diff_section() -> None:
    """M11: the report writer no longer renders the diff section —
    that's now the exporter's job (because the diff task runs
    AFTER the report task in the DAG, so the report can't have
    diff_payload in its inputs at execution time)."""
    agent = ReportWriterAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://example.com/",
            "page_metadata": {
                "url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "title": "Example",
            },
            "seo_report": {"score": 80, "summary": "ok", "issues": []},
            "ui_report": {"score": 70, "summary": "ok", "issues": []},
            "performance_report": {"score": 60, "summary": "ok", "issues": []},
            "a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "security_report": {"score": 70, "summary": "ok", "issues": []},
            "branding_report": {"score": 65, "summary": "ok", "issues": []},
            "diff_against": "20260101T000000Z_example.com",
            "diff_payload": {
                "run_id_a": "20260101T000000Z_example.com",
                "run_id_b": "current",
                "added": [],
                "removed": [],
                "severity_changed": [],
                "score_changed": [],
                "summary": "0 added, 0 removed, 0 severity-changed, 0 score-changed",
            },
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    # The diff section is NOT rendered by the writer. The exporter
    # is responsible for prepending it (see test_exporter).
    assert "## Diff vs" not in md
    # diff_against is also no longer recorded in report_metadata
    # (it lives on the exporter's index row + data.json instead).
    meta = res.metadata.get("report_metadata", res.outputs.get("report_metadata", {}))
    assert "diff_against" not in meta
    assert "diff_summary" not in meta


async def test_no_diff_section_when_diff_against_unset() -> None:
    """Without ``diff_against``, no diff section is rendered."""
    agent = ReportWriterAgent()
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "target_url": "https://example.com/",
            "page_metadata": {
                "url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "title": "Example",
            },
            "seo_report": {"score": 80, "summary": "ok", "issues": []},
            "ui_report": {"score": 70, "summary": "ok", "issues": []},
            "performance_report": {"score": 60, "summary": "ok", "issues": []},
            "a11y_report": {"score": 90, "summary": "ok", "issues": []},
            "security_report": {"score": 70, "summary": "ok", "issues": []},
            "branding_report": {"score": 65, "summary": "ok", "issues": []},
        },
    )
    res = await agent.execute(ctx)
    md = res.outputs["final_report_md"]
    assert "## Diff vs" not in md
    # sub_reports_payload is still populated (the exporter needs it).
    meta = res.metadata.get("report_metadata", res.outputs.get("report_metadata", {}))
    assert "sub_reports_payload" in meta
    assert "seo_report" in meta["sub_reports_payload"]


# M11: the multi-page diff test was moved to test_exporter.py —
# the report writer no longer renders diff sections.
