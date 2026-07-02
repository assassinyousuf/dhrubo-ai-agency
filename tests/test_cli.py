from pathlib import Path

import pytest
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


# ---------------------------------------------------------------------------
# M9 — multi-page audits
# ---------------------------------------------------------------------------


def test_cli_run_audit_accepts_pages_flag() -> None:
    """``--pages`` (comma-separated URLs) is accepted by the CLI."""
    result = runner.invoke(
        app,
        [
            "run-audit",
            "--pages",
            "https://example.com/,https://www.iana.org/",
            "--plan-only",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Pipeline plan OK" in result.stdout


def test_cli_run_audit_rejects_both_url_and_pages() -> None:
    """``--url`` and ``--pages`` are mutually exclusive."""
    result = runner.invoke(
        app,
        [
            "run-audit",
            "--url",
            "https://example.com/",
            "--pages",
            "https://example.com/,https://example.org/",
            "--plan-only",
        ],
    )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "mutually exclusive" in out.lower() or "error" in out.lower()


def test_cli_run_audit_requires_url_or_pages() -> None:
    """Without ``--url`` or ``--pages`` the CLI exits with an error."""
    result = runner.invoke(app, ["run-audit", "--plan-only"])
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "error" in out.lower()


def test_cli_pages_cap_is_25() -> None:
    """``--pages`` rejects lists longer than 25 URLs."""
    urls = ",".join(f"https://example.com/page{i}/" for i in range(26))
    result = runner.invoke(
        app,
        ["run-audit", "--pages", urls, "--plan-only"],
    )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "25" in out or "cap" in out.lower()


# ---------------------------------------------------------------------------
# M10 — comparison / diff runs
# ---------------------------------------------------------------------------


def test_cli_accepts_diff_against_flag() -> None:
    """``--diff-against <id>`` is accepted by the CLI in --plan-only mode."""
    result = runner.invoke(
        app,
        [
            "run-audit",
            "--url",
            "https://example.com/",
            "--diff-against",
            "some_run_id",
            "--plan-only",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Pipeline plan OK" in result.stdout


def test_cli_diff_against_unknown_id_errors() -> None:
    """When the run_id can't be resolved, the CLI exits with an error."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "run-audit",
                "--url",
                "https://example.com/",
                "--diff-against",
                "no_such_run_id",
                "--output-dir",
                tmpdir,
                "--no-pdf",
            ],
        )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "no_such_run_id" in out or "not found" in out.lower()


# ---------------------------------------------------------------------------
# M11 — --diff-since / --diff-until / `dhrubo diff` subcommand
# ---------------------------------------------------------------------------


def test_cli_diff_since_flag_accepted_plan_only() -> None:
    """`--diff-since 7d` should build a workflow with the diff task
    inserted (plan-only mode, no actual resolution needed)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "run-audit",
                "--url",
                "https://example.com/",
                "--diff-since",
                "7d",
                "--plan-only",
                "--output-dir",
                tmpdir,
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert "Pipeline plan OK" in result.stdout


def test_cli_diff_since_and_diff_against_mutually_exclusive() -> None:
    """Passing both `--diff-against` and `--diff-since` is an error."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "run-audit",
                "--url",
                "https://example.com/",
                "--diff-against",
                "some_run_id",
                "--diff-since",
                "7d",
                "--plan-only",
                "--output-dir",
                tmpdir,
            ],
        )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "mutually exclusive" in out


def test_cli_diff_since_bad_format_errors() -> None:
    """Unparseable values fail before any agents run."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "run-audit",
                "--url",
                "https://example.com/",
                "--diff-since",
                "banana",
                "--output-dir",
                tmpdir,
                "--no-pdf",
            ],
        )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "banana" in out or "could not parse" in out.lower()


def test_cli_diff_since_unknown_window_errors() -> None:
    """Empty window (no rows) → CLI exits with a clear error."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "run-audit",
                "--url",
                "https://example.com/",
                "--diff-since",
                "1h",  # no runs in the last hour
                "--output-dir",
                tmpdir,
                "--no-pdf",
            ],
        )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "no runs" in out.lower()


def test_cli_diff_subcommand_requires_url() -> None:
    """`dhrubo diff` requires --url (per-host scope)."""
    result = runner.invoke(
        app,
        ["diff", "--since", "7d"],
    )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "--url" in out or "url" in out.lower()


def test_cli_diff_subcommand_no_runs_in_window_errors() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            app,
            [
                "diff",
                "--url",
                "https://example.com/",
                "--since",
                "1h",
                "--output-dir",
                tmpdir,
            ],
        )
    assert result.exit_code != 0
    out = (result.stdout or "") + (result.stderr or "")
    assert "no runs" in out.lower()


def test_cli_diff_subcommand_with_json() -> None:
    """End-to-end: seed two index.json rows, run the standalone diff
    subcommand with --json, verify a diff_<ts>_<host>.json is written."""
    import json as _json
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Seed two runs for example.com inside the window.
        ts_a = "20260701T110000Z"
        ts_b = "20260701T120000Z"
        for ts in (ts_a, ts_b):
            d = Path(tmpdir) / f"{ts}_example.com"
            d.mkdir(parents=True, exist_ok=True)
            (d / "data.json").write_text(
                _json.dumps(
                    {
                        "sub_reports": {
                            "seo_report": {"score": 80, "issues": []},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (d / "index.json").write_text(
                _json.dumps(
                    [
                        {
                            "run_id": f"{ts}_example.com",
                            "ts": ts,
                            "target_url": "https://example.com/",
                            "target_urls": ["https://example.com/"],
                            "seed_domain": "example.com",
                            "n_pages": 1,
                            "sub_reports_path": str(d / "data.json"),
                            "pages_json_path": None,
                            "diff_against": None,
                        }
                    ]
                ),
                encoding="utf-8",
            )
        result = runner.invoke(
            app,
            [
                "diff",
                "--url",
                "https://example.com/",
                "--since",
                "1d",
                "--until",
                "1h",
                "--json",
                "--output-dir",
                tmpdir,
            ],
        )
        assert result.exit_code == 0, result.stdout
        # The subcommand writes diff_<ts>_<host>.json. The <ts> uses
        # window.start — close enough that any matching diff_*.json
        # file in tmpdir is our output.
        diffs = list(Path(tmpdir).glob("diff_*.json"))
        assert len(diffs) == 1, diffs
        payload = _json.loads(diffs[0].read_text(encoding="utf-8"))
        assert payload["run_id_a"].endswith("_example.com")
        assert payload["run_id_b"].endswith("_example.com")
        assert "summary" in payload


# ---------------------------------------------------------------------------
# M12 — `dhrubo publish` subcommand
# ---------------------------------------------------------------------------


def _diff_payload() -> dict[str, object]:
    return {
        "run_id_a": "previous",
        "run_id_b": "current",
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
        "score_changed": [],
        "summary": "1 added, 0 removed, 0 severity-changed, 0 score-changed",
    }


def test_cli_publish_help() -> None:
    result = runner.invoke(app, ["publish", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "diff.json" in result.stdout
    assert "--github-pr" in result.stdout
    assert "GITHUB_TOKEN" in result.stdout


def test_cli_publish_handles_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--repo",
            "foo/bar",
            "--github-pr",
            "1",
        ],
    )
    assert result.exit_code == 2, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "GITHUB_TOKEN" in out


def test_cli_publish_handles_missing_diff_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            "output/does-not-exist.json",
            "--repo",
            "foo/bar",
            "--github-pr",
            "1",
        ],
    )
    assert result.exit_code == 2, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "diff-path" in out.lower() or "does not exist" in out.lower()


def test_cli_publish_rejects_bad_repo_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--repo",
            "no-slash-here",
            "--github-pr",
            "1",
        ],
    )
    assert result.exit_code == 2, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "owner/name" in out


def test_cli_publish_rejects_zero_pr_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--repo",
            "foo/bar",
            "--github-pr",
            "0",
        ],
    )
    assert result.exit_code == 2, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "github-pr" in out.lower() or "pr" in out.lower()


def test_cli_publish_resolves_repo_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``--repo`` is omitted, the CLI falls back to ``GITHUB_REPOSITORY``."""
    import json as _json

    diff_path = tmp_path / "diff.json"
    diff_path.write_text(_json.dumps(_diff_payload()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/cat")

    # Stub the GitHub tool to capture the call and return a fixed URL.
    from dhrubo.agents import publisher as publisher_mod
    from dhrubo.tools.tool_interface import ToolResult

    captured: list[dict[str, object]] = []

    async def _stub_safe_run(self, raw, ctx):  # type: ignore[no-untyped-def]
        captured.append(dict(raw))
        return ToolResult.ok(
            "github_comment",
            data={"comment_url": "https://x/#1", "id": 1, "repo": raw["repo"]},
            comment_url="https://x/#1",
        )

    monkeypatch.setattr(
        publisher_mod.GitHubCommentTool, "safe_run", _stub_safe_run
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--github-pr",
            "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert len(captured) == 1
    assert captured[0]["repo"] == "octo/cat"


def test_cli_publish_post_command_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end smoke: a valid diff + token + repo/PR → comment_url printed."""
    import json as _json

    diff_path = tmp_path / "diff.json"
    diff_path.write_text(_json.dumps(_diff_payload()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")

    from dhrubo.agents import publisher as publisher_mod
    from dhrubo.tools.tool_interface import ToolResult

    async def _stub_safe_run(self, raw, ctx):  # type: ignore[no-untyped-def]
        return ToolResult.ok(
            "github_comment",
            data={
                "comment_url": "https://github.com/foo/bar/pull/7#issuecomment-99",
                "id": 99,
                "repo": "foo/bar",
            },
            comment_url="https://github.com/foo/bar/pull/7#issuecomment-99",
        )

    monkeypatch.setattr(
        publisher_mod.GitHubCommentTool, "safe_run", _stub_safe_run
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--repo",
            "foo/bar",
            "--github-pr",
            "7",
        ],
    )
    assert result.exit_code == 0, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "issuecomment-99" in out
    assert "Comment posted" in out


def test_cli_publish_surfaces_tool_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 4xx from the underlying GitHub tool surfaces cleanly to the user."""
    import json as _json

    diff_path = tmp_path / "diff.json"
    diff_path.write_text(_json.dumps(_diff_payload()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")

    from dhrubo.agents import publisher as publisher_mod
    from dhrubo.tools.tool_interface import ToolResult

    async def _stub_safe_run(self, raw, ctx):  # type: ignore[no-untyped-def]
        return ToolResult.fail("github_comment", error="HTTP 404: Not Found")

    monkeypatch.setattr(
        publisher_mod.GitHubCommentTool, "safe_run", _stub_safe_run
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--diff-path",
            str(diff_path),
            "--repo",
            "foo/bar",
            "--github-pr",
            "7",
        ],
    )
    assert result.exit_code == 2, result.stdout
    out = (result.stdout or "") + (result.stderr or "")
    assert "404" in out


# ---------------------------------------------------------------------------
# M13 — local web dashboard
# ---------------------------------------------------------------------------


def test_cli_dashboard_help() -> None:
    """``dhrubo dashboard --help`` exits 0 and lists every flag."""
    result = runner.invoke(app, ["dashboard", "--help"])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    for flag in (
        "--host",
        "--port",
        "--output-dir",
        "--config",
        "--open",
        "--workers",
        "--reload",
    ):
        assert flag in out, f"missing {flag} in dashboard --help"


def test_cli_dashboard_requires_subprocess_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If uvicorn isn't importable, the subcommand exits 2 with a
    helpful message (we simulate that by stubbing the import to
    raise ImportError)."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "uvicorn" or name.startswith("uvicorn."):
            raise ImportError("simulated missing uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 2
    out = (result.stdout or "") + (result.stderr or "")
    assert "ui" in out.lower() or "uvicorn" in out.lower()


def test_cli_dashboard_passes_through_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--host`` and ``--port`` flow into the uvicorn config."""
    captured: dict[str, object] = {}

    class _StubServer:
        def __init__(self, config: object) -> None:
            captured["host"] = config.host
            captured["port"] = config.port
            captured["factory"] = config.factory
            captured["app"] = config.app

        def run(self) -> None:
            captured["ran"] = True

    class _StubConfig:
        def __init__(self, app, **kwargs: object) -> None:
            self.app = app
            self.host = kwargs.get("host")
            self.port = kwargs.get("port")
            self.factory = kwargs.get("factory")

    class _StubUvicorn:
        Config = _StubConfig
        Server = _StubServer

    import sys

    monkeypatch.setitem(sys.modules, "uvicorn", _StubUvicorn)

    result = runner.invoke(
        app,
        [
            "dashboard",
            "--host",
            "127.0.0.1",
            "--port",
            "9123",
        ],
    )
    # Server.run() returned without error → exit 0.
    assert result.exit_code == 0, result.stdout
    assert captured.get("ran") is True
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9123
    assert captured["factory"] is True
    assert captured["app"] == "dhrubo.dashboard.app:create_app"


def test_cli_dashboard_runs_against_default_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no flags, the dashboard uses Settings defaults (loopback
    8765, max_concurrent_runs from settings) and exits cleanly."""
    captured: dict[str, object] = {}

    class _StubServer:
        def __init__(self, config: object) -> None:
            captured["host"] = config.host
            captured["port"] = config.port
            captured["factory"] = config.factory
            self._config = config

        def run(self) -> None:
            captured["ran"] = True

    class _StubConfig:
        def __init__(self, app, **kwargs: object) -> None:
            self.app = app
            self.host = kwargs.get("host")
            self.port = kwargs.get("port")
            self.factory = kwargs.get("factory")

    class _StubUvicorn:
        Config = _StubConfig
        Server = _StubServer

    import sys

    monkeypatch.setitem(sys.modules, "uvicorn", _StubUvicorn)

    result = runner.invoke(app, ["dashboard"])
    assert result.exit_code == 0, result.stdout
    assert captured.get("ran") is True
    assert captured["host"] == "127.0.0.1"  # Settings default
    assert captured["port"] == 8765  # Settings default

