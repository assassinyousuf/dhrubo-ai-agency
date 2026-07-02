"""Batch Orchestrator for Dhrubo AI Agency.

Reads a CSV of leads, processes them concurrently using the global BrowserPool,
and outputs a CRM-ready CSV mapping the original leads to the generated artifacts.
"""

import asyncio
import csv
import logging
import os
import sys
from pathlib import Path

from dhrubo.config.loader import load_models_config
from dhrubo.core.logger import setup_logging
from dhrubo.llm import MockProvider
from dhrubo.llm.openai_provider import OpenAICompatibleProvider
from dhrubo.tools.browser_pool import BrowserPool
from dhrubo.workflows.engine import WorkflowEngine
from dhrubo.workflows.website_audit_pipeline import build_website_audit_workflow


def get_provider():
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleProvider()
    return MockProvider()

async def process_url(url: str, sem: asyncio.Semaphore, provider, models_cfg) -> dict:
    async with sem:
        try:
            wf = build_website_audit_workflow(urls=[url])
            engine = WorkflowEngine(max_concurrency=4)
            result = await engine.run(
                wf,
                initial_inputs={"target_url": url, "target_urls": [url], "pdf_enabled": False},
                llm=provider,
                metadata={"models": models_cfg.model_dump()}
            )

            if result.status == "failed":
                # Collect all errors from task_results
                error_msgs = [f"{tid}: {err}" for tid, err in result.errors.items()]
                error_str = " | ".join(error_msgs) if error_msgs else "Unknown workflow error"
                return {"url": url, "status": "failed", "error": error_str}

            # The exporter agent returns `export_paths` containing the output locations
            exports = {}
            if "export" in result.task_results:
                exports = result.task_results["export"].outputs.get("export_paths", {})
            run_dir = exports.get("run_dir", "")

            return {
                "url": url,
                "status": "success",
                "run_dir": run_dir,
                "proposal_path": f"{run_dir}/proposal.md",
                "cold_email_path": f"{run_dir}/cold_email.txt"
            }

        except Exception as e:
            return {"url": url, "status": "failed", "error": str(e)}

async def main():
    setup_logging(level=logging.INFO)
    logger = logging.getLogger("batch_auditor")

    csv_path = Path(r"d:\website analyzer\websitelist csv fies from whatsapp\processed_leads.csv")
    out_csv = Path(r"d:\website analyzer\websitelist csv fies from whatsapp\audited_leads.csv")

    if not csv_path.exists():
        logger.error(f"Input CSV not found at {csv_path}")
        sys.exit(1)

    leads = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(row)

    logger.info(f"Loaded {len(leads)} leads for processing.")

    # Initialize the browser pool
    pool = BrowserPool.get_instance(max_size=10)

    # Restrict concurrent DAG executions to 10
    sem = asyncio.Semaphore(10)
    provider = get_provider()
    models_cfg = load_models_config(Path(r"d:\website analyzer\dhrubo-ai-agency\config"))

    tasks = []
    for lead in leads:
        url = lead.get("Website", "").strip()
        if url.startswith("http"):
            tasks.append((lead, process_url(url, sem, provider, models_cfg)))

    logger.info(f"Starting async execution of {len(tasks)} workflows...")

    # We gather all the audit tasks
    audit_coroutines = [t[1] for t in tasks]
    results = await asyncio.gather(*audit_coroutines, return_exceptions=True)

    # Merge results back into leads
    mapped_leads = []
    for i, (lead, _) in enumerate(tasks):
        res = results[i]
        if isinstance(res, Exception):
            lead["Audit_Status"] = "failed"
            lead["Audit_Error"] = str(res)
        else:
            lead["Audit_Status"] = res.get("status", "unknown")
            lead["Audit_Error"] = res.get("error", "")
            lead["Run_Dir"] = res.get("run_dir", "")
            lead["Proposal"] = res.get("proposal_path", "")
            lead["Cold_Email"] = res.get("cold_email_path", "")
        mapped_leads.append(lead)

    # Write to output CSV
    if mapped_leads:
        fieldnames = list(mapped_leads[0].keys())
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(mapped_leads)

    logger.info(f"Batch complete. Results written to {out_csv}")

    # Graceful shutdown
    await pool.close_all()

if __name__ == "__main__":
    asyncio.run(main())
