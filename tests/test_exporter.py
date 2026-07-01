from pathlib import Path
from typing import Any

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.exporter import ExporterAgent
from dhrubo.tools.markdown_to_pdf_tool import MarkdownToPdfTool

# The "PDF generated" exporter test requires both `weasyprint` and
# `markdown` to be importable on the host; in CI environments without
# the `[pdf]` extra installed it must skip rather than fail.
_pdf_available = pytest.mark.skipif(
    not MarkdownToPdfTool.is_available(),
    reason="weasyprint / markdown not installed; skip PDF generation test",
)


async def test_exporter_writes_report_and_data(tmp_path: Path) -> None:
    agent = ExporterAgent(output_root=tmp_path)
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hello\n",
            "target_url": "https://example.com/?q=test",
            # M6 inputs — opt out of PDF by default in CI envs.
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    report = Path(paths["report_md"])
    data = Path(paths["data_json"])
    assert report.exists()
    assert report.read_text(encoding="utf-8").startswith("# Hello")
    payload = __import__("json").loads(data.read_text(encoding="utf-8"))
    assert payload["target_url"].startswith("https://example.com")
    assert "generated_at" in payload


@_pdf_available
async def test_exporter_writes_pdf_when_tool_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the PDF tool succeeds, ``report_pdf`` appears in ``export_paths``."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=True, pdf_format="a4")
    captured: dict[str, Any] = {}

    async def _render(*, html, base_url, output_path):
        captured["base_url"] = base_url
        captured["output_path"] = output_path
        Path(output_path).write_bytes(b"%PDF-stub")
        return Path(output_path).stat().st_size

    # Patch the bound tool's _do_call — exporter holds its own instance.
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(agent._pdf_tool, "_do_call", _render)

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": True,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    pdf = Path(paths["report_pdf"])
    assert pdf.exists()
    assert pdf.read_bytes() == b"%PDF-stub"
    # base_url is run_dir + "/", forward-slashed
    assert captured["base_url"].endswith("/")


async def test_exporter_skips_pdf_when_tool_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When WeasyPrint isn't installed, the exporter omits the PDF and records
    ``pdf_skipped`` in its metadata — but the audit still succeeds."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=True, pdf_format="a4")
    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: False))

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": True,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    assert "report_pdf" not in paths
    assert res.metadata.get("pdf_skipped", {}).get("reason")
    # The report.md / data.json are always written.
    assert Path(paths["report_md"]).exists()
    assert Path(paths["data_json"]).exists()


async def test_exporter_disables_pdf_when_ctx_says_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``pdf_enabled=False`` input at the DAG layer suppresses PDF output
    even if the agent default would otherwise enable it."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=True, pdf_format="a4")

    # If the tool were called, the test would fail loudly.
    async def _blow(*_args, **_kwargs):
        raise AssertionError("PDF tool must not be called when pdf_enabled=False")

    monkeypatch.setattr(MarkdownToPdfTool, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(agent._pdf_tool, "_do_call", _blow)

    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    assert "report_pdf" not in res.outputs["export_paths"]
    assert "pdf_skipped" not in (res.metadata or {})


# ---------------------------------------------------------------------------
# M9 — multi-page audits
# ---------------------------------------------------------------------------


async def test_exporter_slug_uses_seed_domain(tmp_path: Path) -> None:
    """When ``seed_domain`` is provided, the run-dir slug uses it
    instead of the per-URL target."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "target_urls": ["https://example.com/", "https://example.org/about/"],
            "seed_domain": "example.com",
            "pages": [],
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    run_dir = Path(res.outputs["export_paths"]["run_dir"])
    # The directory name is ``<ts>_<seed_domain>``.
    assert run_dir.parent == tmp_path
    assert run_dir.name.endswith("_example.com")


async def test_exporter_writes_pages_index(tmp_path: Path) -> None:
    """Multi-page runs also write a ``pages.json`` index next to report.md
    and data.json."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://a/",
            "target_urls": ["https://a/", "https://b/"],
            "seed_domain": "a",
            "pages": [
                {"index": 0, "url": "https://a/", "slug": "https_a_"},
                {"index": 1, "url": "https://b/", "slug": "https_b_"},
            ],
            # Per-page metadata that the writer back-fills.
            "page_0_page_metadata": {
                "url": "https://a/",
                "final_url": "https://a/",
                "title": "Homepage A",
            },
            "page_1_page_metadata": {
                "url": "https://b/",
                "final_url": "https://b/path",
                "title": "About B",
            },
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    assert "pages_json" in paths
    pages_path = Path(paths["pages_json"])
    assert pages_path.exists()
    import json as _json

    pages_index = _json.loads(pages_path.read_text(encoding="utf-8"))
    assert len(pages_index) == 2
    assert pages_index[0]["index"] == 0
    assert pages_index[0]["url"] == "https://a/"
    assert pages_index[0]["title"] == "Homepage A"
    assert pages_index[1]["url"] == "https://b/"
    assert pages_index[1]["final_url"] == "https://b/path"
    assert pages_index[1]["title"] == "About B"
    # report.md + data.json are also still written.
    assert Path(paths["report_md"]).exists()
    assert Path(paths["data_json"]).exists()


# ---------------------------------------------------------------------------
# M10 — comparison / diff runs
# ---------------------------------------------------------------------------


async def test_exporter_writes_run_index(tmp_path: Path) -> None:
    """Every export appends a row to ``runs/<ts>_<host>/index.json``."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    index_path = Path(paths["index_json"])
    assert index_path.exists()
    import json as _json

    rows = _json.loads(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert row["target_url"] == "https://example.com/"
    assert row["seed_domain"] is None
    assert row["n_pages"] == 1
    assert row["diff_against"] is None
    assert row["run_id"].endswith("_example.com")
    # Second export appends a second row.
    await agent.execute(ctx)
    rows = _json.loads(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 2


async def test_exporter_writes_sub_reports_into_data_json(tmp_path: Path) -> None:
    """``data.json["sub_reports"]`` carries the structured per-lens payloads."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    sub_reports = {
        "seo_report": {"score": 80, "issues": [{"id": "a:1", "title": "X"}]},
        "security_report": {"score": 70, "issues": []},
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
            "sub_reports": sub_reports,
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    import json as _json

    data = _json.loads(Path(res.outputs["export_paths"]["data_json"]).read_text(encoding="utf-8"))
    assert data["sub_reports"] == sub_reports


async def test_load_sub_reports_for_run_resolves_relative_path(tmp_path: Path) -> None:
    """The CLI resolver must find a run via its ``index.json`` even when
    the stored ``sub_reports_path`` is relative to the run dir (e.g.
    on Windows the path is stored as ``output\\<ts>_<host>\\data.json``,
    which is a *relative* path under the run dir, not an absolute
    filesystem path)."""
    from dhrubo.agents.exporter import load_sub_reports_for_run

    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    sub_reports = {
        "seo_report": {"score": 80, "issues": [{"id": "a:1", "title": "X"}]},
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
            "sub_reports": sub_reports,
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    run_dir = Path(res.outputs["export_paths"]["run_dir"])
    run_id = run_dir.name

    found = load_sub_reports_for_run(run_id, tmp_path)
    assert found is not None
    assert "seo_report" in found
    assert found["seo_report"]["issues"][0]["id"] == "a:1"


async def test_exporter_writes_diff_json_when_diff_payload_set(tmp_path: Path) -> None:
    """When ``diff_payload`` is in inputs, the exporter writes ``diff.json``."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    diff_payload = {
        "run_id_a": "p",
        "run_id_b": "c",
        "added": [{"lens": "seo_report", "page": None, "issue": {"id": "a:1"}}],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
        "summary": "1 added, 0 removed, 0 severity-changed, 0 score-changed",
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Hi\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
            "diff_payload": diff_payload,
            "diff_against": "p",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    assert "diff_json" in paths
    diff_path = Path(paths["diff_json"])
    assert diff_path.exists()
    import json as _json

    written = _json.loads(diff_path.read_text(encoding="utf-8"))
    assert written["summary"] == diff_payload["summary"]
    # The run index also records the diff_against reference.
    index_rows = _json.loads(Path(paths["index_json"]).read_text(encoding="utf-8"))
    assert index_rows[-1]["diff_against"] == "p"


# ---------------------------------------------------------------------------
# M11 — exporter renders the diff section into report.md
# ---------------------------------------------------------------------------


async def test_exporter_prepends_diff_section_to_report(tmp_path: Path) -> None:
    """The exporter is responsible for prepending the
    ``## Diff vs <run_id>`` section to ``report.md`` (M11 fix).

    The DAG shape ``report → diff → export`` means the diff is
    only available at export time — moving the rendering here
    fixes a latent M10 bug where the section never appeared in
    the live pipeline.
    """
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    diff_payload = {
        "run_id_a": "p",
        "run_id_b": "c",
        "added": [
            {
                "lens": "seo_report",
                "page": None,
                "issue": {
                    "id": "missing-meta:abc12345",
                    "severity": "major",
                    "title": "Missing meta description",
                    "detail": "…",
                    "recommendation": "…",
                },
            }
        ],
        "removed": [],
        "severity_changed": [],
        "score_changed": [
            {
                "lens": "seo_report",
                "page": None,
                "score_a": 80,
                "score_b": 75,
                "delta": -5,
            }
        ],
        "summary": "1 added, 0 removed, 0 severity-changed, 1 score-changed",
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": (
                "# Website Audit Report\n\n## Page Snapshot\n\n"
                "placeholder\n"
            ),
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
            "diff_payload": diff_payload,
            "diff_against": "p",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    report_md = Path(paths["report_md"]).read_text(encoding="utf-8")
    # Diff H2 is prepended.
    assert "## Diff vs `p`" in report_md
    # Diff appears before the rest of the body.
    diff_idx = report_md.find("## Diff vs")
    snapshot_idx = report_md.find("## Page Snapshot")
    assert 0 <= diff_idx < snapshot_idx
    assert "1 added" in report_md
    assert "Score (was" in report_md


async def test_exporter_no_diff_section_when_unset(tmp_path: Path) -> None:
    """Without ``diff_payload``, report.md is unchanged."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    body = "# Website Audit Report\n\n## Page Snapshot\n\nplaceholder\n"
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": body,
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    report_md = Path(paths["report_md"]).read_text(encoding="utf-8")
    assert "## Diff vs" not in report_md
    # Original body preserved verbatim.
    assert report_md == body


async def test_exporter_multi_page_diff_groups_by_page(tmp_path: Path) -> None:
    """Multi-page diff rows carry a ``page`` key; the exporter
    groups them per-page via ``render_diff_section(multi_page=True)``."""
    agent = ExporterAgent(output_root=tmp_path, pdf_enabled=False, pdf_format="a4")
    diff_payload = {
        "run_id_a": "previous_run",
        "run_id_b": "current",
        "added": [
            {
                "lens": "seo_report",
                "page": "0",
                "issue": {
                    "id": "missing-h1:a1",
                    "severity": "minor",
                    "title": "Page 0 missing H1",
                    "detail": "…",
                    "recommendation": "…",
                },
            },
            {
                "lens": "security_report",
                "page": "1",
                "issue": {
                    "id": "missing-csp:b2",
                    "severity": "critical",
                    "title": "Page 1 missing CSP",
                    "detail": "…",
                    "recommendation": "…",
                },
            },
        ],
        "removed": [],
        "severity_changed": [],
        "score_changed": [],
        "summary": "2 added, 0 removed, 0 severity-changed, 0 score-changed",
    }
    ctx = AgentContext(
        role=agent.role,
        inputs={
            "final_report_md": "# Audit\n\n## Summary\nplaceholder\n",
            "target_url": "https://example.com/",
            "pdf_enabled": False,
            "pdf_format": "a4",
            "pages": [
                {"index": 0, "url": "https://a/", "slug": "https_a_"},
                {"index": 1, "url": "https://b/", "slug": "https_b_"},
            ],
            "diff_payload": diff_payload,
            "diff_against": "previous_run",
        },
    )
    res = await agent.execute(ctx)
    assert res.success
    paths = res.outputs["export_paths"]
    report_md = Path(paths["report_md"]).read_text(encoding="utf-8")
    assert "## Diff vs `previous_run`" in report_md
    # Multi-page: rows grouped per-page with bold headers.
    assert "**Page 1**" in report_md
    assert "**Page 2**" in report_md
    assert "Page 0 missing H1" in report_md
    assert "Page 1 missing CSP" in report_md
