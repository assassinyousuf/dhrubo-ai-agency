"""`ExporterAgent` — writes the final report to disk (and optionally other formats)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from dhrubo.agents.base_agent import AgentContext, AgentResult, BaseAgent
from dhrubo.agents.report_writer import render_diff_section
from dhrubo.core.logger import get_logger
from dhrubo.core.run_index import load_run_index, load_sub_reports_for_run
from dhrubo.core.slug import safe_slug
from dhrubo.tools.markdown_to_pdf_tool import MarkdownToPdfTool
from dhrubo.tools.tool_interface import ToolContext

# Re-exported for backward compat (M10/M11 callers may import
# these directly from ``agents.exporter``).
__all__ = [
    "ExporterAgent",
    "load_run_index",
    "load_sub_reports_for_run",
]

_log = get_logger("agents.exporter")


def _safe_dir_name(url: str) -> str:
    """Backwards-compatible thin wrapper around :func:`safe_slug`."""
    return safe_slug(url)


def _write_run_index(
    index_path: Path,
    *,
    run_id: str,
    ts: str,
    target_url: str,
    target_urls: list[str],
    seed_domain: str | None,
    n_pages: int,
    sub_reports_path: str,
    pages_json_path: str | None,
    diff_against: str | None,
) -> None:
    """Create-or-append one row to the per-host ``index.json``.

    Schema::

        [
          {"run_id", "ts", "target_url", "target_urls", "seed_domain",
           "n_pages", "sub_reports_path", "pages_json_path", "diff_against"},
          ...
        ]
    """
    if index_path.exists():
        try:
            rows = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (json.JSONDecodeError, OSError):
            rows = []
    else:
        rows = []
    rows.append(
        {
            "run_id": run_id,
            "ts": ts,
            "target_url": target_url,
            "target_urls": list(target_urls),
            "seed_domain": seed_domain,
            "n_pages": n_pages,
            "sub_reports_path": sub_reports_path,
            "pages_json_path": pages_json_path,
            "diff_against": diff_against,
        }
    )
    index_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class ExporterAgent(BaseAgent):
    role: ClassVar[str] = "exporter"
    input_keys: ClassVar[tuple[str, ...]] = (
        "final_report_md",
        "target_url",
        "target_urls",
        "seed_domain",
        "pages",
        "pdf_format",
        "pdf_enabled",
        # M10 additions.
        "sub_reports",
        "diff_payload",
        "diff_against",
    )
    output_keys: ClassVar[tuple[str, ...]] = ("export_paths",)

    def __init__(
        self,
        output_root: Path | None = None,
        *,
        pdf_enabled: bool = True,
        pdf_format: Literal["a4", "letter"] = "a4",
    ) -> None:
        self._root = output_root or Path("./output")
        self._pdf_enabled = bool(pdf_enabled)
        self._pdf_format: Literal["a4", "letter"] = pdf_format
        self._pdf_tool = MarkdownToPdfTool()

    async def execute(self, ctx: AgentContext) -> AgentResult:
        report: str = ctx.inputs.get("final_report_md") or ""
        target_url = ctx.inputs.get("target_url") or "report"
        seed_domain: str | None = ctx.inputs.get("seed_domain")
        pages: list[dict[str, object]] = list(ctx.inputs.get("pages") or [])
        sub_reports: dict[str, Any] = dict(ctx.inputs.get("sub_reports") or {})
        diff_payload: dict[str, Any] | None = ctx.inputs.get("diff_payload")
        diff_against: str | None = ctx.inputs.get("diff_against")

        # ---- M11: prepend the `## Diff vs <run_id>` H2 section
        # here (not in the report writer) because the diff task
        # runs AFTER the report task in the M10/M11 DAG. The
        # exporter is the only task that has both `final_report_md`
        # and `diff_payload` in its inputs at the same time. ----
        multi_page_render = len(pages) >= 2
        if diff_payload and diff_against:
            diff_lines: list[str] = []
            render_diff_section(
                diff_lines,
                diff_payload,
                str(diff_against),
                multi_page=multi_page_render,
            )
            report = "\n".join(diff_lines).rstrip() + "\n\n" + report

        # Per-run overrides via the DAG inputs (e.g. CLI flags).
        run_pdf_enabled = bool(ctx.inputs.get("pdf_enabled", self._pdf_enabled))
        ctx_pdf_format = ctx.inputs.get("pdf_format", self._pdf_format)
        run_pdf_format: Literal["a4", "letter"] = (
            ctx_pdf_format if ctx_pdf_format in ("a4", "letter") else self._pdf_format
        )

        # Run-dir slug: prefer seed_domain (multi-page) over the legacy
        # target_url-derived slug. Either way, sanitize via the shared
        # safe_slug helper.
        slug_source = seed_domain or str(target_url)
        ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir_name = f"{ts}_{_safe_dir_name(slug_source)}"
        run_id = run_dir_name  # run_id == directory name (stable, unique)
        run_dir = self._root / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)

        report_path = run_dir / "report.md"
        data_path = run_dir / "data.json"

        report_path.write_text(report, encoding="utf-8")
        payload = {
            "target_url": str(target_url),
            "target_urls": list(ctx.inputs.get("target_urls") or []),
            "seed_domain": seed_domain,
            "pages": pages,
            "sub_reports": sub_reports,
            "diff_against": diff_against,
            "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "report_markdown": report,
            "context_metadata": dict(ctx.metadata or {}),
        }
        data_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # Multi-page: also write a pages.json index for downstream tooling.
        pages_path: Path | None = None
        if pages:
            pages_index = []
            for i, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                url = page.get("url", "")
                final_url = ""
                title = ""
                # Try to back-fill final_url + title from the namespaced
                # page_metadata payload (writer of that data is the
                # per-URL crawl task).
                meta_payload: Any = ctx.inputs.get(f"page_{i}_page_metadata") or {}
                if isinstance(meta_payload, dict):
                    final_url = str(meta_payload.get("final_url", "") or url)
                    title = str(meta_payload.get("title", "") or "")
                pages_index.append(
                    {
                        "index": i,
                        "url": url,
                        "final_url": final_url or url,
                        "title": title,
                    }
                )
            pages_path = run_dir / "pages.json"
            pages_path.write_text(
                json.dumps(pages_index, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        export_paths: dict[str, str] = {
            "report_md": str(report_path),
            "data_json": str(data_path),
            "run_dir": str(run_dir),
        }
        if pages_path is not None:
            export_paths["pages_json"] = str(pages_path)

        # M10: write a diff.json next to data.json when a diff was
        # computed for this run.
        diff_path: Path | None = None
        if diff_payload is not None:
            diff_path = run_dir / "diff.json"
            diff_path.write_text(
                json.dumps(diff_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            export_paths["diff_json"] = str(diff_path)

        # M10: append a row to the per-host run index so future
        # ``--diff-against`` lookups can resolve this run.
        index_path = run_dir / "index.json"
        try:
            # n_pages: prefer the explicit `pages` list (multi-page
            # shape); fall back to 1 for single-page runs even when
            # sub_reports is empty.
            n_pages = (
                len(pages)
                if pages
                else (1 if not sub_reports else len(sub_reports))
            )
            _write_run_index(
                index_path,
                run_id=run_id,
                ts=ts,
                target_url=str(target_url),
                target_urls=list(ctx.inputs.get("target_urls") or []),
                seed_domain=seed_domain,
                n_pages=n_pages,
                sub_reports_path=str(data_path),
                pages_json_path=str(pages_path) if pages_path else None,
                diff_against=diff_against,
            )
            export_paths["index_json"] = str(index_path)
        except OSError as exc:  # pragma: no cover - disk full, etc.
            _log.warning(
                "exporter.index_write_failed",
                extra={"role": self.role, "error": str(exc)},
            )

        result_metadata: dict[str, object] = {}

        if run_pdf_enabled:
            pdf_path = run_dir / "report.pdf"
            tool_ctx = ToolContext(requester_role=self.role)
            tool_result = await self._pdf_tool.safe_run(
                {
                    "markdown": report,
                    "output_path": str(pdf_path),
                    # WeasyPrint's `base_url` resolves relative image refs
                    # (e.g. `screenshots\foo.png`); use forward slashes so
                    # the same code works on Windows and POSIX.
                    "base_url": (str(run_dir) + "/").replace("\\", "/"),
                    "page_size": run_pdf_format,
                    "title": f"Audit \u2014 {target_url}",
                },
                tool_ctx,
            )
            if tool_result.success and not (tool_result.data or {}).get("skipped"):
                export_paths["report_pdf"] = str(pdf_path)
            else:
                reason = (
                    (tool_result.data or {}).get("reason")
                    if tool_result.data
                    else tool_result.error
                ) or "pdf generation skipped"
                _log.info(
                    "exporter.pdf_skipped",
                    extra={"role": self.role, "reason": reason, "run_dir": str(run_dir)},
                )
                result_metadata["pdf_skipped"] = {"reason": reason}

        result = AgentResult.ok(self.role, export_paths=export_paths)
        if result_metadata:
            result.metadata.update(result_metadata)
        return result
