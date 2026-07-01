import asyncio
import os
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dhrubo.commands.cli import run_audit
from dhrubo.core.logger import get_logger

_log = get_logger("api.server")

app = FastAPI(title="Dhrubo AI Agency API")

# Define the paths
ROOT_DIR = Path(__file__).parent.parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
OUTPUT_DIR = ROOT_DIR / "output"

class AuditRequest(BaseModel):
    url: str
    pdf: bool = False
    diff_since: str | None = None

def run_audit_task(url: str, pdf: bool, diff_since: str | None) -> None:
    """Wrapper to run the audit synchronously in the background task thread."""
    try:
        _log.info(f"Starting background audit for {url}")
        # Note: We must run this in a fresh event loop or just call the cli function directly.
        # cli.run_audit is synchronous, but uses asyncio.run internally.
        # Since BackgroundTasks runs in a separate thread, asyncio.run should work perfectly.
        run_audit(
            url=url,
            pages=None,
            config_dir=ROOT_DIR / "config",
            plan_only_flag=False,
            dry_run=False,
            output_dir=OUTPUT_DIR,
            pdf=pdf,
            pdf_format="a4",
            max_concurrency=4,
            diff_against=None,
            diff_since=diff_since,
            diff_until=None
        )
        _log.info(f"Finished background audit for {url}")
    except Exception as e:
        _log.error(f"Audit failed for {url}: {e}")

@app.post("/api/audit")
async def start_audit(req: AuditRequest, background_tasks: BackgroundTasks):
    if not req.url:
        raise HTTPException(status_code=400, detail="url is required")
    
    background_tasks.add_task(run_audit_task, req.url, req.pdf, req.diff_since)
    return {"message": "Audit started in background", "url": req.url}

@app.get("/api/runs")
async def get_runs():
    """Scan the output directory for completed runs."""
    runs = []
    if not OUTPUT_DIR.exists():
        return {"runs": runs}
        
    for run_dir in OUTPUT_DIR.iterdir():
        if not run_dir.is_dir():
            continue
            
        index_file = run_dir / "index.json"
        data_file = run_dir / "data.json"
        if data_file.exists():
            # Minimal metadata
            runs.append({
                "run_id": run_dir.name,
                "created_at": run_dir.name.split("_")[0],
                "host": run_dir.name.split("_", 1)[1] if "_" in run_dir.name else run_dir.name,
                "has_diff": (run_dir / "diff.json").exists()
            })
    
    # Sort descending by run_id (timestamp)
    runs.sort(key=lambda x: x["run_id"], reverse=True)
    return {"runs": runs}

@app.get("/api/runs/{run_id}")
async def get_run_details(run_id: str):
    run_dir = OUTPUT_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
        
    data_file = run_dir / "data.json"
    if not data_file.exists():
        raise HTTPException(status_code=404, detail="data.json not found")
        
    try:
        data = json.loads(data_file.read_text("utf-8"))
        
        diff_data = None
        diff_file = run_dir / "diff.json"
        if diff_file.exists():
            diff_data = json.loads(diff_file.read_text("utf-8"))
            
        return {
            "run_id": run_id,
            "data": data,
            "diff": diff_data,
            "report_md": data.get("report_markdown", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mount frontend
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def read_root():
        return {"message": "Frontend not built yet. Create 'frontend' directory with index.html"}
