"""The Website Audit pipeline definition.

This module is the canonical example of a workflow in Dhrubo.

Single-page (M8 topology, ``urls=None`` or a length-1 list)::

    plan
        │
        ▼
    page_indexer                  (single source of truth for N + URLs)
        │
        ▼
    website_crawler ─┬─► screenshot_agent ──┬─► ui_reviewer
                      │                      │
                      ├─► seo_reviewer ──────┤
                      │                      │
                      ├─► performance_review ┤
                      │                      │
                      ├─► accessibility_review
                      │                      │
                      ├─► security_review ───┤
                      │                      │
                      └─► branding_review ───┘
                                             ▼
                                        report_writer
                                             │
                                             ▼
                                          exporter

Multi-page (M9 shape, N URLs)::

    plan
        │
        ▼
    page_indexer
        │
        ├─► crawl_0 ─┬─► screenshots_0 ─┬─► seo_0 … branding_0 ─┐
        │            │                  │                       │
        ├─► crawl_1 ─┤                  ├─► seo_1 … branding_1 ─┤
        │            │                  │                       │
        │    …       │                  │    …                  │
        │            │                  │                       │
        └─► crawl_{N-1} ─┴─► screenshots_{N-1} ─┴─► seo_{N-1} … branding_{N-1} ─┘
                                                                              ▼
                                                                            report
                                                                              │
                                                                              ▼
                                                                          export

Each per-URL fan-out uses ``metadata={"inputs": {"target_url": urls[i]}}``
so the engine injects the per-page URL into ``ctx.inputs["target_url"]``
without re-architecting the per-task inputs contract.
"""

from __future__ import annotations

from dhrubo.core.logger import get_logger
from dhrubo.workflows.engine import Workflow
from dhrubo.workflows.task import Task

_log = get_logger("pipelines.website_audit")


# Per-URL task specs: (suffix, role, input_keys, output_keys).
_PER_URL_TASKS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("crawl", "website_crawler", ("target_url",), ("dom_html", "page_metadata")),
    ("screenshots", "screenshot_agent", ("target_url",), ("screenshot_paths",)),
    ("seo_review", "seo_reviewer", ("dom_html", "page_metadata"), ("seo_report",)),
    ("ui_review", "ui_reviewer", ("screenshot_paths", "page_metadata"), ("ui_report",)),
    (
        "perf_review",
        "performance_reviewer",
        ("target_url", "page_metadata"),
        ("performance_report",),
    ),
    (
        "a11y_review",
        "accessibility_reviewer",
        ("target_url", "page_metadata"),
        ("a11y_report",),
    ),
    (
        "security_review",
        "security_reviewer",
        ("target_url", "page_metadata"),
        ("security_report",),
    ),
    (
        "branding_review",
        "branding_reviewer",
        ("target_url", "page_metadata", "dom_html"),
        ("branding_report",),
    ),
)


def build_website_audit_workflow(
    urls: list[str] | None = None,
    *,
    diff_against: str | None = None,
) -> Workflow:
    """Construct the DAG for a single or multi-page website audit run.

    Single-page (the default — ``urls=None`` or a length-1 list) preserves
    the M8 task IDs verbatim. Multi-page fans out per URL.

    When ``diff_against`` is set (M10), an extra ``diff`` task is
    inserted between ``report`` and ``export``. The diff task depends
    on the current run's ``sub_reports`` (written by the report task)
    plus the previous run's ``previous_sub_reports`` (resolved by the
    CLI and injected via ``Task.metadata["inputs"]``).
    """
    wf = Workflow(name="website_audit")

    urls = urls or [""]
    n = len(urls)
    multi = n >= 2

    # ------------------------------------------------------------------
    # 1) planner.
    # ------------------------------------------------------------------
    wf.add(
        Task(
            task_id="plan",
            role="planner",
            input_keys=("target_url",),
            output_keys=("plan",),
        )
    )

    # ------------------------------------------------------------------
    # 2) page_indexer — single source of truth for N + URLs.
    # ------------------------------------------------------------------
    wf.add(
        Task(
            task_id="page_indexer",
            role="page_indexer",
            depends_on=["plan"],
            input_keys=("target_url", "target_urls"),
            output_keys=("pages", "seed_domain"),
        )
    )

    # ------------------------------------------------------------------
    # 3) Per-URL fan-out (single or multi).
    # ------------------------------------------------------------------
    report_deps: list[str] = []
    report_input_keys: list[str] = ["pages", "seed_domain"]

    def _make_id(suffix: str, index: int | None) -> str:
        return suffix if index is None else f"{suffix}_{index}"

    def _add(
        suffix: str,
        role: str,
        in_keys: tuple[str, ...],
        out_keys: tuple[str, ...],
        index: int | None,
        url: str,
        depends_on: list[str],
    ) -> str:
        tid = _make_id(suffix, index)
        wf.add(
            Task(
                task_id=tid,
                role=role,
                depends_on=depends_on,
                input_keys=in_keys,
                output_keys=out_keys,
                metadata={"inputs": {"target_url": url}},
            )
        )
        return tid

    if not multi:
        # ---- single-page: M8 task IDs verbatim ----
        url0 = urls[0]
        crawl_id = _add("crawl", "website_crawler", ("target_url",), ("dom_html", "page_metadata"), None, url0, ["page_indexer"])
        screenshots_id = _add(
            "screenshots",
            "screenshot_agent",
            ("target_url",),
            ("screenshot_paths",),
            None,
            url0,
            ["page_indexer", crawl_id],
        )
        # M8 layout: most reviewers depend on `crawl` (they need page_metadata
        # / dom_html / target_url, which the crawler produces). The UI + a11y
        # reviewers hang off `screenshots` (they need a browser session
        # and/or screenshot_paths).
        _screenshots_deps = {"ui_review", "a11y_review"}
        for suffix, role, in_keys, out_keys in _PER_URL_TASKS[2:]:
            upstream = screenshots_id if suffix in _screenshots_deps else crawl_id
            tid = _add(suffix, role, in_keys, out_keys, None, url0, [upstream])
            report_deps.append(tid)
            report_input_keys.extend(out_keys)
        # The report also reads the page metadata + dom_html + screenshots.
        report_input_keys.extend(("page_metadata", "dom_html", "screenshot_paths"))
        report_deps.extend([crawl_id, screenshots_id])
    else:
        # ---- multi-page: per-URL fan-out ----
        _screenshots_deps_multi = {"ui_review", "a11y_review"}
        for i, url in enumerate(urls):
            crawl_id = _add(
                "crawl", "website_crawler", ("target_url",),
                (f"page_{i}_dom_html", f"page_{i}_page_metadata"),
                i, url, ["page_indexer"],
            )
            screenshots_id = _add(
                "screenshots", "screenshot_agent", ("target_url",),
                (f"page_{i}_screenshot_paths",),
                i, url, [crawl_id],
            )
            for suffix, role, _, _ in _PER_URL_TASKS[2:]:
                # Namespaced output keys for each per-URL reviewer.
                base_out = {
                    "seo_review": ("seo_report",),
                    "ui_review": ("ui_report",),
                    "perf_review": ("performance_report",),
                    "a11y_review": ("a11y_report",),
                    "security_review": ("security_report",),
                    "branding_review": ("branding_report",),
                }[suffix]
                namespaced_out = tuple(f"page_{i}_{k}" for k in base_out)
                upstream = screenshots_id if suffix in _screenshots_deps_multi else crawl_id
                tid = _add(
                    suffix, role, ("target_url", "pages", "seed_domain"),
                    namespaced_out,
                    i, url, [upstream],
                )
                report_deps.append(tid)
                report_input_keys.extend(namespaced_out)
            report_input_keys.extend(
                (f"page_{i}_dom_html", f"page_{i}_page_metadata", f"page_{i}_screenshot_paths")
            )
            report_deps.append(crawl_id)
            report_deps.append(screenshots_id)

    # ------------------------------------------------------------------
    # 4) report — aggregator.
    # ------------------------------------------------------------------
    report_output_keys: tuple[str, ...] = ("final_report_md", "sub_reports")
    report_task = Task(
        task_id="report",
        role="report_writer",
        depends_on=sorted(set(report_deps)),
        input_keys=tuple(dict.fromkeys(report_input_keys)),  # de-dup, preserve order
        output_keys=report_output_keys,
    )
    wf.add(report_task)

    # ------------------------------------------------------------------
    # 5) diff — M10 aggregator (only when --diff-against is set).
    # ------------------------------------------------------------------
    if diff_against:
        wf.add(
            Task(
                task_id="diff",
                role="diff_reviewer",
                depends_on=["report"],
                input_keys=(
                    "sub_reports",
                    "previous_sub_reports",
                    "diff_against",
                    "current_run_id",
                ),
                output_keys=("diff_payload",),
                # Inject the previous run's sub_reports + diff_against via
                # the engine's metadata-driven input merge (no engine change).
                metadata={
                    "inputs": {
                        "diff_against": diff_against,
                    },
                },
            )
        )

    # ------------------------------------------------------------------
    # 6) exporter.
    # ------------------------------------------------------------------
    export_deps = ["report"]
    if diff_against:
        export_deps.append("diff")
    export_input_keys: tuple[str, ...] = (
        "final_report_md",
        "target_url",
        "target_urls",
        "seed_domain",
        "pages",
        "pdf_format",
        "pdf_enabled",
        "sub_reports",
    )
    if diff_against:
        export_input_keys = (*export_input_keys, "diff_payload", "diff_against")
    wf.add(
        Task(
            task_id="export",
            role="exporter",
            depends_on=export_deps,
            input_keys=export_input_keys,
            output_keys=("export_paths",),
        )
    )
    return wf


def plan_only() -> None:
    """Build the workflow (single-page default) and validate its DAG shape.

    Safe to call now.
    """
    wf = build_website_audit_workflow()
    wf.validate()
    _log.info(
        "pipeline.built",
        extra={"workflow": wf.name, "tasks": [t.task_id for t in wf.tasks]},
    )
