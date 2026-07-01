from dhrubo.commands.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Dhrubo" in result.stdout


def test_cli_run_audit_plan_only() -> None:
    result = runner.invoke(
        app,
        ["run-audit", "--url", "https://example.com", "--plan-only"],
    )
    assert result.exit_code == 0, result.stdout
    assert "Pipeline plan OK" in result.stdout


def test_cli_plan_command() -> None:
    result = runner.invoke(app, ["plan"])
    assert result.exit_code == 0
    assert "Pipeline plan OK" in result.stdout
