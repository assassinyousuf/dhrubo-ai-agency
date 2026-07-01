import pytest
from dhrubo.workflows.engine import Workflow
from dhrubo.workflows.task import Task
from dhrubo.workflows.task_queue import InProcessTaskQueue
from dhrubo.workflows.website_audit_pipeline import (
    build_website_audit_workflow,
    plan_only,
)


def test_workflow_dag_validates_a_cyclic() -> None:
    wf = Workflow(name="t")
    wf.add(Task(task_id="a", role="x"))
    wf.add(Task(task_id="b", role="y", depends_on=["a"]))
    wf.validate()  # should not raise


def test_workflow_dag_detects_cycle() -> None:
    from dhrubo.core.errors import WorkflowError

    wf = Workflow(name="t")
    wf.add(Task(task_id="a", role="x"))
    wf.add(Task(task_id="b", role="y", depends_on=["a"]))
    # Make 'a' depend on 'b' (only feasible post-hoc via __dict__ mutation).
    wf.tasks[0].depends_on.append("b")
    with pytest.raises(WorkflowError):
        wf.validate()


def test_plan_only_website_audit_runs() -> None:
    plan_only()  # raises on invalid DAG


def test_website_audit_dag_shape() -> None:
    wf = build_website_audit_workflow()
    wf.validate()
    ids = {t.task_id for t in wf.tasks}
    # M2 ships a slim pipeline; later milestones will add more nodes.
    expected = {"plan", "crawl", "seo_review", "report", "export"}
    assert expected.issubset(ids)


def test_ui_review_node_is_in_dag() -> None:
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "ui_review" in ids
    ui = next(t for t in wf.tasks if t.task_id == "ui_review")
    assert "screenshots" in ui.depends_on
    assert ui.role == "ui_reviewer"
    assert "ui_report" in ui.output_keys


def test_report_waits_on_ui_review() -> None:
    wf = build_website_audit_workflow()
    report = next(t for t in wf.tasks if t.task_id == "report")
    assert {"screenshots", "seo_review", "ui_review"} <= set(report.depends_on)
    assert "ui_report" in report.input_keys


def test_workflow_validates_after_m4() -> None:
    """The full M4 DAG must still be acyclic and dependency-consistent."""
    wf = build_website_audit_workflow()
    wf.validate()  # must not raise


def test_perf_review_node_in_dag() -> None:
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "perf_review" in ids
    perf = next(t for t in wf.tasks if t.task_id == "perf_review")
    assert "crawl" in perf.depends_on
    assert perf.role == "performance_reviewer"
    assert "performance_report" in perf.output_keys


def test_report_waits_on_perf_review() -> None:
    wf = build_website_audit_workflow()
    report = next(t for t in wf.tasks if t.task_id == "report")
    assert {"screenshots", "seo_review", "ui_review", "perf_review"} <= set(report.depends_on)
    assert "performance_report" in report.input_keys


def test_workflow_validates_after_m5() -> None:
    """The full M5 DAG must still be acyclic and dependency-consistent."""
    wf = build_website_audit_workflow()
    wf.validate()  # must not raise


def test_export_task_accepts_pdf_keys() -> None:
    """M6: the exporter accepts pdf_format + pdf_enabled inputs."""
    wf = build_website_audit_workflow()
    exporter = next(t for t in wf.tasks if t.task_id == "export")
    assert "pdf_format" in exporter.input_keys
    assert "pdf_enabled" in exporter.input_keys


def test_workflow_validates_after_m6() -> None:
    """The full M6 DAG must still be acyclic and dependency-consistent."""
    wf = build_website_audit_workflow()
    wf.validate()  # must not raise


def test_a11y_review_node_in_dag() -> None:
    """M7: the accessibility reviewer node hangs off `screenshots`
    (it needs a browser session)."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "a11y_review" in ids
    a11y = next(t for t in wf.tasks if t.task_id == "a11y_review")
    assert "screenshots" in a11y.depends_on
    assert a11y.role == "accessibility_reviewer"
    assert "a11y_report" in a11y.output_keys
    assert "target_url" in a11y.input_keys
    assert "page_metadata" in a11y.input_keys


def test_report_waits_on_a11y_review() -> None:
    """M7: the report task must wait on the accessibility reviewer and
    include `a11y_report` in its input keys."""
    wf = build_website_audit_workflow()
    report = next(t for t in wf.tasks if t.task_id == "report")
    assert {"screenshots", "seo_review", "ui_review", "perf_review", "a11y_review"} <= set(
        report.depends_on
    )
    assert "a11y_report" in report.input_keys


def test_workflow_validates_after_m7() -> None:
    """The full M7 DAG must still be acyclic and dependency-consistent."""
    wf = build_website_audit_workflow()
    wf.validate()  # must not raise


def test_security_review_node_in_dag() -> None:
    """M8: the security reviewer hangs off `crawl` (needs page_metadata)."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "security_review" in ids
    sec = next(t for t in wf.tasks if t.task_id == "security_review")
    assert "crawl" in sec.depends_on
    assert sec.role == "security_reviewer"
    assert "security_report" in sec.output_keys
    assert "target_url" in sec.input_keys
    assert "page_metadata" in sec.input_keys


def test_branding_review_node_in_dag() -> None:
    """M8: the branding reviewer hangs off `crawl` (needs page_metadata + dom_html)."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "branding_review" in ids
    brand = next(t for t in wf.tasks if t.task_id == "branding_review")
    assert "crawl" in brand.depends_on
    assert brand.role == "branding_reviewer"
    assert "branding_report" in brand.output_keys
    assert "target_url" in brand.input_keys
    assert "page_metadata" in brand.input_keys
    assert "dom_html" in brand.input_keys


def test_report_waits_on_security_and_branding() -> None:
    """M8: the report task must wait on security + branding and
    include `security_report` + `branding_report` in its input keys."""
    wf = build_website_audit_workflow()
    report = next(t for t in wf.tasks if t.task_id == "report")
    assert {
        "screenshots",
        "seo_review",
        "ui_review",
        "perf_review",
        "a11y_review",
        "security_review",
        "branding_review",
    } <= set(report.depends_on)
    assert "security_report" in report.input_keys
    assert "branding_report" in report.input_keys


def test_workflow_validates_after_m8() -> None:
    """The full M8 DAG must still be acyclic and dependency-consistent."""
    wf = build_website_audit_workflow()
    wf.validate()  # must not raise


# ---------------------------------------------------------------------------
# M9 — multi-page audits
# ---------------------------------------------------------------------------


def test_page_indexer_node_in_dag() -> None:
    """M9: a ``page_indexer`` task precedes the crawler (uniform input shape)."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "page_indexer" in ids
    idx = next(t for t in wf.tasks if t.task_id == "page_indexer")
    assert "plan" in idx.depends_on
    assert idx.role == "page_indexer"
    assert "pages" in idx.output_keys
    assert "seed_domain" in idx.output_keys


def test_single_page_dag_preserves_m8_task_ids() -> None:
    """The M8 task IDs (no _0/_1 suffix) must still exist on the single-page path."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    # The M8 task IDs (without suffix) must still exist.
    assert "crawl" in ids
    assert "screenshots" in ids
    assert "seo_review" in ids
    assert "ui_review" in ids
    assert "perf_review" in ids
    assert "a11y_review" in ids
    assert "security_review" in ids
    assert "branding_review" in ids
    assert "report" in ids
    assert "export" in ids


def test_multi_page_dag_creates_per_url_tasks() -> None:
    """M9: a 3-URL audit produces 1 page_indexer + 3x8 per-URL tasks + report + export."""
    wf = build_website_audit_workflow(urls=["https://a/", "https://b/", "https://c/"])
    wf.validate()
    ids = {t.task_id for t in wf.tasks}
    assert "page_indexer" in ids
    # 8 per-URL task kinds x 3 URLs = 24 namespaced tasks.
    for i in (0, 1, 2):
        for suffix in (
            "crawl", "screenshots", "seo_review", "ui_review",
            "perf_review", "a11y_review", "security_review", "branding_review",
        ):
            assert f"{suffix}_{i}" in ids, f"missing {suffix}_{i}"
    # Single-page task IDs are NOT in the multi-page DAG.
    assert "crawl" not in ids
    assert "screenshots" not in ids
    # Aggregator + exporter.
    assert "report" in ids
    assert "export" in ids


def test_per_url_tasks_inject_target_url() -> None:
    """Each per-URL crawl task carries the right URL in its ``metadata.inputs``."""
    wf = build_website_audit_workflow(urls=["https://a/", "https://b/"])
    for i, expected in enumerate(["https://a/", "https://b/"]):
        crawl = next(t for t in wf.tasks if t.task_id == f"crawl_{i}")
        assert crawl.metadata.get("inputs", {}).get("target_url") == expected
        screenshots = next(t for t in wf.tasks if t.task_id == f"screenshots_{i}")
        assert screenshots.metadata.get("inputs", {}).get("target_url") == expected


def test_report_aggregates_all_pages() -> None:
    """The report task depends on every per-URL reviewer and the indexer."""
    wf = build_website_audit_workflow(urls=["https://a/", "https://b/"])
    report = next(t for t in wf.tasks if t.task_id == "report")
    # 8 per-URL task kinds x 2 URLs = 16 namespaced task IDs should appear in deps.
    per_url_dep_count = sum(
        1 for d in report.depends_on
        if any(d.startswith(prefix) for prefix in (
            "crawl_", "screenshots_", "seo_review_", "ui_review_",
            "perf_review_", "a11y_review_", "security_review_", "branding_review_",
        ))
    )
    assert per_url_dep_count == 16
    # Report reads pages + seed_domain + per-page sub-reports.
    assert "pages" in report.input_keys
    assert "seed_domain" in report.input_keys
    assert "page_0_seo_report" in report.input_keys
    assert "page_1_seo_report" in report.input_keys


def test_export_task_accepts_seed_domain_and_pages() -> None:
    """M9: the exporter reads ``seed_domain`` + ``pages`` + ``target_urls``."""
    wf = build_website_audit_workflow(urls=["https://a/", "https://b/"])
    exporter = next(t for t in wf.tasks if t.task_id == "export")
    assert "seed_domain" in exporter.input_keys
    assert "pages" in exporter.input_keys
    assert "target_urls" in exporter.input_keys


def test_workflow_validates_after_m9() -> None:
    """The full M9 DAG (multi-page) must still be acyclic."""
    wf = build_website_audit_workflow(urls=["https://a/", "https://b/", "https://c/"])
    wf.validate()  # must not raise


def test_in_process_queue_roundtrip() -> None:
    import asyncio

    async def _go() -> None:
        q: InProcessTaskQueue = InProcessTaskQueue()
        t = Task(task_id="t", role="r")
        await q.put(t)
        got = await q.get()
        assert got.task_id == "t"
        await q.mark_done(got)

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# M10 — comparison / diff runs
# ---------------------------------------------------------------------------


def test_diff_task_in_dag_when_diff_against_set() -> None:
    """M10: ``--diff-against`` inserts a ``diff`` task between report and export."""
    wf = build_website_audit_workflow(diff_against="some_run_id")
    wf.validate()
    ids = {t.task_id for t in wf.tasks}
    assert "diff" in ids
    diff = next(t for t in wf.tasks if t.task_id == "diff")
    assert diff.role == "diff_reviewer"
    assert "diff_payload" in diff.output_keys
    assert "sub_reports" in diff.input_keys
    assert "previous_sub_reports" in diff.input_keys


def test_no_diff_task_when_diff_against_unset() -> None:
    """M10: without ``--diff-against``, the DAG has no ``diff`` task."""
    wf = build_website_audit_workflow()
    ids = {t.task_id for t in wf.tasks}
    assert "diff" not in ids


def test_diff_task_depends_on_report() -> None:
    """M10: the diff task depends on ``report`` (needs the current
    run's ``sub_reports``) and feeds ``export``."""
    wf = build_website_audit_workflow(diff_against="x")
    diff = next(t for t in wf.tasks if t.task_id == "diff")
    assert "report" in diff.depends_on
    exporter = next(t for t in wf.tasks if t.task_id == "export")
    assert "diff" in exporter.depends_on


def test_export_task_reads_diff_payload() -> None:
    """M10: the export task reads ``diff_payload`` + ``diff_against``."""
    wf = build_website_audit_workflow(diff_against="x")
    exporter = next(t for t in wf.tasks if t.task_id == "export")
    assert "diff_payload" in exporter.input_keys
    assert "diff_against" in exporter.input_keys
    assert "sub_reports" in exporter.input_keys


def test_diff_task_works_for_multi_page() -> None:
    """M10: multi-page diff runs also get the ``diff`` task."""
    wf = build_website_audit_workflow(
        urls=["https://a/", "https://b/"],
        diff_against="x",
    )
    wf.validate()
    ids = {t.task_id for t in wf.tasks}
    assert "diff" in ids
    diff = next(t for t in wf.tasks if t.task_id == "diff")
    assert "report" in diff.depends_on
