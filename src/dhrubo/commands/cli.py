"""Dhrubo CLI — entry point for the Website Audit pipeline (and future commands)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Literal, cast

import typer
from rich.console import Console
from rich.panel import Panel

from dhrubo import agents as _agents
from dhrubo.agents.base_agent import agent_registry
from dhrubo.agents.exporter import ExporterAgent
from dhrubo.agents.publisher import PublisherAgent
from dhrubo.config.loader import build_settings, load_models_config
from dhrubo.config.settings import Settings
from dhrubo.core.errors import DhruboError
from dhrubo.core.logger import get_logger, setup_logging
from dhrubo.core.run_index import load_sub_reports_for_run
from dhrubo.core.run_window import select_runs_in_window
from dhrubo.core.timeparse import parse_window
from dhrubo.llm import MockProvider
from dhrubo.llm.interface import ILLMProvider
from dhrubo.llm.openai_provider import OpenAICompatibleProvider
from dhrubo.tools.diff_tool import compute_diff
from dhrubo.workflows.engine import WorkflowEngine, WorkflowStatus
from dhrubo.workflows.website_audit_pipeline import (
    build_website_audit_workflow,
    plan_only,
)

app = typer.Typer(
    name="dhrubo",
    help="Dhrubo AI Agency — autonomous Website Audit Agent.",
    no_args_is_help=True,
    add_completion=False,
)

_console = Console()
_log = get_logger("cli")


# ----------------------------------------------------------------------
# Helpers (kept module-level so tests can call them too).
# ----------------------------------------------------------------------


def _initialize(settings: Settings) -> None:
    setup_logging(settings.logging.level)


def build_llm_provider(*, force_mock: bool = False) -> ILLMProvider:
    """Return the LLM provider based on env vars.

    Falls back to :class:`MockProvider` if no real provider can be created.
    """
    if force_mock:
        _log.info("llm.using_mock", extra={"reason": "forced"})
        return MockProvider()

    if os.environ.get("OPENAI_API_KEY"):
        try:
            return OpenAICompatibleProvider()
        except Exception:  # pragma: no cover
            _log.warning("llm.openai_init_failed", exc_info=True)

    _log.info("llm.using_mock", extra={"reason": "no_api_key"})
    return MockProvider()


def register_configured_exporter(
    output_dir: Path,
    *,
    pdf_enabled: bool = True,
    pdf_format: str = "a4",
) -> None:
    """Register an ExporterAgent subclass that writes to ``output_dir``."""

    pdf_format_literal: Literal["a4", "letter"] = "letter" if pdf_format == "letter" else "a4"

    class _ConfiguredExporter(ExporterAgent):
        role = "exporter"
        input_keys = (
            "final_report_md",
            "target_url",
            "pdf_format",
            "pdf_enabled",
        )
        output_keys = ("export_paths",)

        def __init__(self) -> None:
            ExporterAgent.__init__(
                self,
                output_root=output_dir,
                pdf_enabled=pdf_enabled,
                pdf_format=pdf_format_literal,
            )

    agent_registry.register(_ConfiguredExporter)


# ----------------------------------------------------------------------
# Commands.
# ----------------------------------------------------------------------


@app.command("run-audit")
def run_audit(
    url: str | None = typer.Option(
        None,
        "--url",
        "-u",
        help="Target website URL to audit. Mutually exclusive with --pages.",
    ),
    pages: str | None = typer.Option(
        None,
        "--pages",
        help=(
            "Comma-separated list of URLs to audit in one run. When set, runs "
            "a multi-page audit and overrides --url. Cap: 25 URLs. For "
            "multi-page runs, pass --concurrency 8-12 to parallelize per-URL tasks."
        ),
    ),
    config_dir: Path = typer.Option(
        Path("./config"),
        "--config",
        "-c",
        help="Directory holding YAML config files.",
        exists=False,
        file_okay=False,
    ),
    plan_only_flag: bool = typer.Option(
        False, "--plan-only", help="Validate the pipeline DAG and exit without running agents."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build the pipeline and log what would happen, but do not run."
    ),
    output_dir: Path = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Directory to write report artifacts into.",
        file_okay=False,
    ),
    pdf: bool = typer.Option(
        True,
        "--pdf/--no-pdf",
        help="Render a report.pdf alongside report.md (requires `pip install -e .[pdf]`).",
    ),
    pdf_format: str = typer.Option(
        "a4",
        "--pdf-format",
        help="PDF page size: a4 or letter.",
        case_sensitive=False,
    ),
    max_concurrency: int = typer.Option(4, "--concurrency", min=1, max=64),
    diff_against: str | None = typer.Option(
        None,
        "--diff-against",
        help=(
            "Compare this run against a previous run_id (e.g. "
            "'20260702T120000Z_example.com'). Resolved via "
            "runs/<host>/index.json. Emits a '## Diff vs <run_id>' "
            "section in report.md and a diff.json next to data.json. "
            "Mutually exclusive with --diff-since."
        ),
    ),
    diff_since: str | None = typer.Option(
        None,
        "--diff-since",
        help=(
            "Compare this run against the earliest run whose "
            "timestamp falls in a time window. Accepts relative "
            "(7d, 24h, 1w) or absolute (YYYY-MM-DD or "
            "YYYY-MM-DDTHH:MM:SSZ) values. Pair with --diff-until "
            "(default: now). Resolves via the per-host run index "
            "and funnels into the same --diff-against path. "
            "Mutually exclusive with --diff-against."
        ),
    ),
    diff_until: str | None = typer.Option(
        None,
        "--diff-until",
        help=(
            "Upper bound for the --diff-since window. Same format as "
            "--diff-since. Defaults to 'now' when omitted."
        ),
    ),
) -> None:
    """Run the Website Audit pipeline.

    Pass either ``--url`` (single page, the default) or ``--pages <a,b,c>``
    (multi-page, comma-separated; cap 25).
    """
    settings = build_settings(config_dir=config_dir)
    _initialize(settings)
    _agents.ensure_all_registered()

    # Validate --url / --pages.
    if not url and not pages:
        _console.print(
            "[red]Error:[/red] provide either --url or --pages."
        )
        raise typer.Exit(code=2)
    if url and pages:
        _console.print(
            "[red]Error:[/red] --url and --pages are mutually exclusive."
        )
        raise typer.Exit(code=2)
    if diff_against and diff_since:
        _console.print(
            "[red]Error:[/red] --diff-against and --diff-since are "
            "mutually exclusive."
        )
        raise typer.Exit(code=2)

    target_urls: list[str]
    if pages:
        target_urls = [u.strip() for u in pages.split(",") if u.strip()]
        if not target_urls:
            _console.print("[red]Error:[/red] --pages is empty.")
            raise typer.Exit(code=2)
        if len(target_urls) > 25:
            _console.print(
                f"[red]Error:[/red] --pages cap is 25 URLs (got {len(target_urls)})."
            )
            raise typer.Exit(code=2)
        target_url = target_urls[0]
    else:
        target_url = url or ""
        target_urls = [target_url]

    # ---- M11: --diff-since resolves into a concrete diff_against
    # below, AFTER the plan-only short-circuit (same shape as M10's
    # --diff-against). During plan-only we build the workflow with
    # the diff task skipped; only the actual run resolves the
    # window. ----
    target_output = output_dir or settings.output.directory
    previous_sub_reports: dict[str, object] | None = None
    effective_diff_against = diff_against
    if plan_only_flag or dry_run:
        # Don't try to resolve --diff-since during plan-only — we
        # just want to validate the workflow shape. Build it as if
        # no diff were requested.
        effective_diff_against = None
    elif diff_since and not diff_against:
        try:
            window = parse_window(diff_since, diff_until)
        except ValueError as exc:
            _console.print(f"[red]Error:[/red] could not parse --diff-since/--diff-until: {exc}")
            raise typer.Exit(code=2) from exc
        rows = select_runs_in_window(
            window,
            target_url=target_url,
            output_root=target_output,
        )
        if not rows:
            _console.print(
                f"[red]Error:[/red] no runs found in the window "
                f"[{window.start.isoformat()}, {window.end.isoformat()}) "
                f"for url={target_url!r}."
            )
            raise typer.Exit(code=2)
        if len(rows) == 1:
            _console.print(
                f"[yellow]Warning:[/yellow] only one run in the window "
                f"({rows[0]['run_id']!r}); running without a diff section."
            )
        else:
            effective_diff_against = str(rows[0]["run_id"])
            _console.print(
                f"[cyan]Diff:[/cyan] --diff-since resolved to '{effective_diff_against}'."
            )

    workflow = build_website_audit_workflow(
        urls=target_urls, diff_against=effective_diff_against
    )
    if plan_only_flag or dry_run:
        # Validate the workflow we actually built (single- or multi-page).
        workflow.validate()
        _log.info(
            "pipeline.built",
            extra={
                "workflow": workflow.name,
                "tasks": [t.task_id for t in workflow.tasks],
            },
        )
        _console.print(
            Panel.fit(
                f"[green]Pipeline plan OK[/green]: {len(workflow.tasks)} tasks.",
                title=f"workflow: {workflow.name}",
            )
        )
        return

    pdf_format_norm = pdf_format.strip().lower()
    if pdf_format_norm not in {"a4", "letter"}:
        _console.print(
            f"[red]Error:[/red] --pdf-format must be 'a4' or 'letter' (got {pdf_format!r})."
        )
        raise typer.Exit(code=2)

    _console.print(
        f"[bold]Starting Website Audit for[/bold] {len(target_urls)} page(s): "
        f"{', '.join(target_urls)}"
    )

    # ---- M10/M11: resolve the effective --diff-against to a
    # previous sub_reports dict (M11's --diff-since resolves to a
    # concrete run_id above). ----
    if effective_diff_against:
        previous_sub_reports = load_sub_reports_for_run(
            effective_diff_against, target_output
        )
        if previous_sub_reports is None:
            _console.print(
                f"[red]Error:[/red] run_id '{effective_diff_against}' not found in any "
                f"index.json under {target_output}."
            )
            raise typer.Exit(code=2)
        _console.print(
            f"[cyan]Diff:[/cyan] comparing against previous run '{effective_diff_against}'."
        )

    provider = build_llm_provider()

    register_configured_exporter(
        target_output,
        pdf_enabled=pdf,
        pdf_format=pdf_format_norm,
    )

    models_cfg = load_models_config(config_dir)
    engine = WorkflowEngine(max_concurrency=max_concurrency)

    initial_inputs: dict[str, object] = {
        "target_url": target_url,
        "target_urls": target_urls,
        "pdf_enabled": pdf,
        "pdf_format": pdf_format_norm,
    }
    if effective_diff_against and previous_sub_reports is not None:
        initial_inputs["diff_against"] = effective_diff_against
        initial_inputs["previous_sub_reports"] = previous_sub_reports

    result = asyncio.run(
        engine.run(
            workflow,
            initial_inputs=initial_inputs,
            llm=provider,
            metadata={"models": models_cfg.model_dump()},
        )
    )

    status_color = {
        WorkflowStatus.COMPLETED: "green",
        WorkflowStatus.PARTIAL: "yellow",
        WorkflowStatus.FAILED: "red",
    }.get(result.status, "white")
    _console.print(
        Panel.fit(
            f"Workflow [bold]{result.workflow}[/bold] finished with status "
            f"[{status_color}]{result.status.value}[/{status_color}].",
            title="Audit complete",
        )
    )
    if "export" in result.task_results:
        paths = result.task_results["export"].outputs.get("export_paths", {})
        if paths:
            _console.print(f"Report written to [cyan]{paths.get('report_md')}[/cyan]")
            pdf_path = paths.get("report_pdf")
            if pdf_path:
                _console.print(f"PDF written to [cyan]{pdf_path}[/cyan]")
        skipped = (result.task_results["export"].metadata or {}).get("pdf_skipped")
        if skipped:
            _console.print(
                f"[yellow]PDF skipped:[/yellow] {skipped.get('reason', 'no reason given')}"
            )


@app.command("plan")
def plan(
    config_dir: Path = typer.Option(
        Path("./config"), "--config", "-c", exists=False, file_okay=False
    ),
) -> None:
    """Validate the Website Audit DAG without running it."""
    settings = build_settings(config_dir=config_dir)
    _initialize(settings)
    plan_only()
    _console.print("[green]Pipeline plan OK[/green]")


@app.command("diff")
def diff_cmd(
    url: str | None = typer.Option(
        None,
        "--url",
        "-u",
        help="Filter by target_url or seed_domain. Required for per-host diffs.",
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help=(
            "Window start. Accepts relative (7d, 24h, 1w) or "
            "absolute (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ) values. "
            "Defaults to '7d ago' when omitted."
        ),
    ),
    until: str | None = typer.Option(
        None,
        "--until",
        help=(
            "Window end. Same format as --since. Defaults to 'now' "
            "when omitted."
        ),
    ),
    output_dir: Path = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Output directory (defaults to ./output). Only used with --json.",
        file_okay=False,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Write diff.json under <output-dir> instead of printing a summary.",
    ),
    config_dir: Path = typer.Option(
        Path("./config"),
        "--config",
        "-c",
        exists=False,
        file_okay=False,
    ),
) -> None:
    """Compute a diff between the earliest and latest run in a time
    window.

    Pure-history query against ``runs/<host>/index.json`` — no
    audit is run, no agents spin up. Reuses the same ``DiffTool``
    that powers ``run-audit --diff-against``.

    With ``--json``, writes ``diff_<ts>_<host>.json`` under the
    output directory. Otherwise prints a per-lens summary on stdout.
    """
    settings = build_settings(config_dir=config_dir)
    _initialize(settings)

    if not url:
        _console.print(
            "[red]Error:[/red] --url is required (per-host diff scope)."
        )
        raise typer.Exit(code=2)

    try:
        window = parse_window(since, until)
    except ValueError as exc:
        _console.print(f"[red]Error:[/red] could not parse --since/--until: {exc}")
        raise typer.Exit(code=2) from exc

    target_output = output_dir or settings.output.directory
    rows = select_runs_in_window(
        window,
        target_url=url,
        output_root=target_output,
    )
    if not rows:
        _console.print(
            f"[red]Error:[/red] no runs found in window "
            f"[{window.start.isoformat()}, {window.end.isoformat()}) "
            f"for url={url!r}."
        )
        raise typer.Exit(code=2)
    if len(rows) == 1:
        _console.print(
            f"[yellow]Warning:[/yellow] only one run in the window "
            f"({rows[0]['run_id']!r}); emitting an empty diff."
        )

    earliest = rows[0]
    latest = rows[-1]
    previous = load_sub_reports_for_run(str(earliest["run_id"]), target_output)
    current = load_sub_reports_for_run(str(latest["run_id"]), target_output)
    diff = compute_diff(
        run_id_a=str(earliest["run_id"]),
        run_id_b=str(latest["run_id"]),
        sub_reports_a=previous or {},
        sub_reports_b=current or {},
    )

    if json_output:
        target_output.mkdir(parents=True, exist_ok=True)
        host = (latest.get("seed_domain") or url).replace("/", "_")
        ts_part = window.start.strftime("%Y%m%dT%H%M%SZ")
        path = target_output / f"diff_{ts_part}_{host}.json"
        path.write_text(
            json.dumps(diff, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _console.print(f"Wrote [cyan]{path}[/cyan]")
        return

    _print_diff_summary(
        earliest=earliest,
        latest=latest,
        diff=diff,
        window=window,
        url=url,
    )


def _print_diff_summary(
    *,
    earliest: dict[str, object],
    latest: dict[str, object],
    diff: dict[str, object],
    window: object,
    url: str,
) -> None:
    """Render the human-friendly summary the standalone ``diff``
    subcommand prints when ``--json`` is omitted."""
    summary = str(diff.get("summary", ""))
    added_raw = diff.get("added")
    removed_raw = diff.get("removed")
    severity_raw = diff.get("severity_changed")
    score_raw = diff.get("score_changed")
    added: list[dict[str, object]] = added_raw if isinstance(added_raw, list) else []
    removed: list[dict[str, object]] = removed_raw if isinstance(removed_raw, list) else []
    severity_changed: list[dict[str, object]] = (
        severity_raw if isinstance(severity_raw, list) else []
    )
    score_changed: list[dict[str, object]] = (
        score_raw if isinstance(score_raw, list) else []
    )

    # Per-lens tallies.
    lenses = ("seo_report", "ui_report", "performance_report",
              "a11y_report", "security_report", "branding_report")
    per_lens: dict[str, dict[str, int]] = {
        lens: {"added": 0, "removed": 0, "score_delta": 0} for lens in lenses
    }
    for row in added:
        lens = str(row.get("lens", ""))
        if lens in per_lens:
            per_lens[lens]["added"] += 1
    for row in removed:
        lens = str(row.get("lens", ""))
        if lens in per_lens:
            per_lens[lens]["removed"] += 1
    for row in score_changed:
        lens = str(row.get("lens", ""))
        if lens in per_lens:
            delta: object = row.get("delta", 0)
            with contextlib.suppress(TypeError, ValueError):
                per_lens[lens]["score_delta"] += int(cast(int, delta or 0))

    lines: list[str] = []
    lines.append(f"Diff [cyan]{earliest.get('run_id', '?')}[/cyan] -> [cyan]{latest.get('run_id', '?')}[/cyan]")
    lines.append(
        f"  Window: {_human_duration(window)}  "
        f"({len(added)} added, {len(removed)} removed, "
        f"{len(severity_changed)} severity-changed, "
        f"{len(score_changed)} score-changed)"
    )
    lines.append(f"  Host: {url}  Runs compared: {earliest.get('run_id')} .. {latest.get('run_id')}")
    lines.append(f"  Summary: {summary}")
    if added or removed or score_changed:
        lines.append("  Per-lens breakdown:")
        lens_titles = {
            "seo_report": "SEO Review",
            "ui_report": "UI Review",
            "performance_report": "Performance Review",
            "a11y_report": "Accessibility Review",
            "security_report": "Security Review",
            "branding_report": "Branding Review",
        }
        for lens in lenses:
            tally = per_lens[lens]
            if not (tally["added"] or tally["removed"] or tally["score_delta"]):
                continue
            sign = "+" if tally["score_delta"] >= 0 else ""
            lines.append(
                f"    {lens_titles[lens]:<22}  "
                f"+{tally['added']} -{tally['removed']}  "
                f"Δscore {sign}{tally['score_delta']}"
            )
    _console.print("\n".join(lines))


def _human_duration(window: object) -> str:
    """Render a :class:`Window` as a short human-readable span."""
    end = getattr(window, "end", None)
    start = getattr(window, "start", None)
    if start is None or end is None:
        return "(unknown)"
    delta = end - start
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


@app.command("publish")
def publish_cmd(
    diff_path: Path = typer.Option(
        ...,
        "--diff-path",
        help=(
            "Path to a diff.json file on disk. Typically the "
            "`diff.json` written by `run-audit --diff-since` or the "
            "output of `dhrubo diff --json`."
        ),
        exists=False,
        file_okay=True,
        dir_okay=False,
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        help=(
            "GitHub repo (`owner/name`) to post the comment on. "
            "Falls back to the `GITHUB_REPOSITORY` env var when "
            "omitted (set automatically by GitHub Actions)."
        ),
    ),
    pr_number: int | None = typer.Option(
        None,
        "--github-pr",
        help="PR number to comment on. Required.",
    ),
    max_issues_per_lens: int = typer.Option(
        50,
        "--max-issues-per-lens",
        min=0,
        max=500,
        help=(
            "Cap on issues listed per lens in the rendered Markdown. "
            "0 = summary table only, no per-issue details."
        ),
    ),
    config_dir: Path = typer.Option(
        Path("./config"),
        "--config",
        "-c",
        help="Directory holding YAML config files.",
        exists=False,
        file_okay=False,
    ),
) -> None:
    """Post a diff.json as a Markdown comment on a GitHub PR.

    Pure publisher — no audit re-runs. Reads the diff from disk,
    renders it via :mod:`dhrubo.tools.markdown_diff_renderer`, and
    posts it via the GitHub REST API.

    The ``GITHUB_TOKEN`` env var must hold a PAT or installation
    token with `repo` scope; the CLI errors cleanly when unset.
    """
    settings = build_settings(config_dir=config_dir)
    _initialize(settings)

    # ---- Resolve repo (flag -> env) ----------------------------------
    repo_env_name = settings.github.repository_env
    resolved_repo = repo or os.environ.get(repo_env_name)
    if not resolved_repo:
        _console.print(
            f"[red]Error:[/red] --repo is required (or set the "
            f"{repo_env_name} env var)."
        )
        raise typer.Exit(code=2)
    # Sanity check: "owner/name" shape.
    if "/" not in resolved_repo or resolved_repo.count("/") != 1:
        _console.print(
            f"[red]Error:[/red] --repo must be of the form 'owner/name', "
            f"got {resolved_repo!r}."
        )
        raise typer.Exit(code=2)

    # ---- Resolve PR number ------------------------------------------
    if pr_number is None or pr_number < 1:
        _console.print(
            "[red]Error:[/red] --github-pr must be a positive integer."
        )
        raise typer.Exit(code=2)

    # ---- Resolve token (env-only on purpose: never via flag so the
    # secret doesn't end up in shell history) --------------------------
    token_env_name = settings.github.api_key_env
    token = os.environ.get(token_env_name)
    if not token:
        _console.print(
            f"[red]Error:[/red] {token_env_name} env var is not set. "
            "Set it to a GitHub PAT or installation token with `repo` scope."
        )
        raise typer.Exit(code=2)

    # ---- Validate the diff path -------------------------------------
    if not diff_path.exists() or not diff_path.is_file():
        _console.print(
            f"[red]Error:[/red] --diff-path does not exist or is not a "
            f"file: {diff_path}"
        )
        raise typer.Exit(code=2)

    _console.print(
        f"[bold]Publishing diff[/bold] {diff_path} -> [cyan]{resolved_repo}[/cyan] PR #{pr_number}"
    )

    publisher = PublisherAgent(config_dir=config_dir)
    ctx_obj = _agents.base_agent.AgentContext(
        role=publisher.role,
        inputs={
            "diff_path": diff_path,
            "repo": resolved_repo,
            "pr_number": pr_number,
            "github_token": token,
            "max_issues_per_lens": max_issues_per_lens,
        },
    )
    result = asyncio.run(publisher.safe_execute(ctx_obj))

    if not result.success:
        _console.print(f"[red]Error:[/red] {result.error or 'publish failed'}")
        raise typer.Exit(code=2)

    comment_url = str(result.outputs.get("comment_url", ""))
    _console.print(
        Panel.fit(
            f"Comment posted: [cyan]{comment_url}[/cyan]",
            title=f"{resolved_repo} PR #{pr_number}",
        )
    )


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        envvar="DHRUBO_LOG_LEVEL",
        help="Override the log level (DEBUG/INFO/WARNING/ERROR).",
    ),
) -> None:
    """Global CLI options."""
    if version:
        from dhrubo import __version__

        typer.echo(f"dhrubo {__version__}")
        raise typer.Exit()
    if log_level is not None:
        setup_logging(log_level)


def main() -> None:
    """Wrapper for ``python -m dhrubo.commands.cli`` and the console script."""
    try:
        app()
    except DhruboError as exc:
        _console.print(f"[red]Error:[/red] {exc.message}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    main()
