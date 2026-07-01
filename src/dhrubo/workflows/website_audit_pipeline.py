"""The Website Audit pipeline definition.

This module is the canonical example of a workflow in Dhrubo.

Milestone 2 ships a thin vertical slice: planner → crawler → SEO reviewer
→ report writer → exporter. Additional reviewers (UI, performance,
accessibility, security, branding) and the QA gate are added in later
milestones. The DAG will simply grow more nodes against the same engine.
"""

from __future__ import annotations

from dhrubo.core.logger import get_logger
from dhrubo.workflows.engine import Workflow
from dhrubo.workflows.task import Task

_log = get_logger("pipelines.website_audit")


def build_website_audit_workflow() -> Workflow:
    """Construct the DAG for a single website audit run.

    Current M3 topology::

        planner
            │
            ▼
        website_crawler ─┬─► screenshot_agent
                          │
                          ▼
                       seo_reviewer
                          │
                          ▼
                       report_writer
                          │
                          ▼
                       exporter
    """
    wf = Workflow(name="website_audit")

    wf.add(
        Task(
            task_id="plan",
            role="planner",
            input_keys=("target_url",),
            output_keys=("plan",),
        )
    )
    wf.add(
        Task(
            task_id="crawl",
            role="website_crawler",
            depends_on=["plan"],
            input_keys=("target_url",),
            output_keys=("dom_html", "page_metadata"),
        )
    )
    wf.add(
        Task(
            task_id="screenshots",
            role="screenshot_agent",
            depends_on=["crawl"],
            input_keys=("target_url",),
            output_keys=("screenshot_paths",),
        )
    )
    wf.add(
        Task(
            task_id="seo_review",
            role="seo_reviewer",
            depends_on=["crawl"],
            input_keys=("dom_html", "page_metadata"),
            output_keys=("seo_report",),
        )
    )
    wf.add(
        Task(
            task_id="report",
            role="report_writer",
            depends_on=["screenshots", "seo_review"],
            input_keys=("seo_report", "page_metadata", "screenshot_paths"),
            output_keys=("final_report_md",),
        )
    )
    wf.add(
        Task(
            task_id="export",
            role="exporter",
            depends_on=["report"],
            input_keys=("final_report_md", "target_url"),
            output_keys=("export_paths",),
        )
    )
    return wf


def plan_only() -> None:
    """Build the workflow and validate its DAG shape. Safe to call now."""
    wf = build_website_audit_workflow()
    wf.validate()
    _log.info(
        "pipeline.built",
        extra={"workflow": wf.name, "tasks": [t.task_id for t in wf.tasks]},
    )
