"""Dhrubo CLI — entry point for the Website Audit pipeline (and future commands)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from dhrubo import agents as _agents
from dhrubo.agents.base_agent import agent_registry
from dhrubo.agents.exporter import ExporterAgent
from dhrubo.config.loader import build_settings, load_models_config
from dhrubo.config.settings import Settings
from dhrubo.core.errors import DhruboError
from dhrubo.core.logger import get_logger, setup_logging
from dhrubo.llm import MockProvider
from dhrubo.llm.interface import ILLMProvider
from dhrubo.llm.openai_provider import OpenAICompatibleProvider
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


def register_configured_exporter(output_dir: Path) -> None:
    """Register an ExporterAgent subclass that writes to ``output_dir``."""

    class _ConfiguredExporter(ExporterAgent):
        role = "exporter"
        input_keys = ("final_report_md", "target_url")
        output_keys = ("export_paths",)

        def __init__(self) -> None:
            ExporterAgent.__init__(self, output_root=output_dir)

    agent_registry.register(_ConfiguredExporter)


# ----------------------------------------------------------------------
# Commands.
# ----------------------------------------------------------------------


@app.command("run-audit")
def run_audit(
    url: str = typer.Option(..., "--url", "-u", help="Target website URL to audit."),
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
    max_concurrency: int = typer.Option(4, "--concurrency", min=1, max=64),
) -> None:
    """Run the Website Audit pipeline against ``--url``."""
    settings = build_settings(config_dir=config_dir)
    _initialize(settings)
    _agents.ensure_all_registered()

    workflow = build_website_audit_workflow()
    if plan_only_flag or dry_run:
        plan_only()
        _console.print(
            Panel.fit(
                f"[green]Pipeline plan OK[/green]: {len(workflow.tasks)} tasks.",
                title=f"workflow: {workflow.name}",
            )
        )
        return

    _console.print(f"[bold]Starting Website Audit for[/bold] {url}")
    provider = build_llm_provider()

    target_output = output_dir or settings.output.directory
    register_configured_exporter(target_output)

    models_cfg = load_models_config(config_dir)
    engine = WorkflowEngine(max_concurrency=max_concurrency)

    result = asyncio.run(
        engine.run(
            workflow,
            initial_inputs={"target_url": url},
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
