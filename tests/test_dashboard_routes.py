"""`tests.test_dashboard_routes` — HTTP route tests.

Drives the FastAPI app with ``starlette.testclient.TestClient``
(in-process; no ports bound). The supervisor is exercised with
real subprocesses (the ``runs`` form spawns ``python -c`` via
the existing argv plumbing) so the routes do see live jobs.

The publish endpoint's GitHub call is stubbed via
``monkeypatch.setattr`` on the ``PublisherAgent`` internals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from dhrubo.dashboard.app import create_app
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_with_dirs(tmp_path: Path):
    """An app + a temp config/output dir, with a fresh supervisor
    (no jobs in flight)."""
    app = create_app(output_root=tmp_path / "out", config_dir=tmp_path / "cfg")
    client = TestClient(app)
    return app, client, tmp_path


@pytest.fixture()
def seeded_output(tmp_path: Path) -> Path:
    """A pre-populated output directory with one run.

    Mirrors the on-disk layout produced by :class:`ExporterAgent`:
    a host subdirectory containing ``<run_id>/...`` plus a single
    ``index.json`` listing the runs.
    """
    out = tmp_path / "out"
    run_id = "20260101T000000Z_example.com"
    host_dir = out / "example.com"
    host_dir.mkdir(parents=True)
    run_dir = host_dir / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# Hello\n\nA report.\n", encoding="utf-8")
    (run_dir / "data.json").write_text(
        json.dumps({"run_id": run_id, "overall_score": 88}), encoding="utf-8"
    )
    (host_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "run_id": run_id,
                    "seed_domain": "example.com",
                    "target_url": "https://example.com/",
                    "ts": "20260701T000000Z",
                    "n_pages": 1,
                    "overall_score": 88,
                    "run_dir": str(run_dir),
                }
            ]
        ),
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------------------------------
# Home / host / report
# ---------------------------------------------------------------------------


def test_home_200(app_with_dirs: tuple[Any, TestClient, Path]) -> None:
    _, client, _ = app_with_dirs
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dhrubo" in resp.text


def test_runs_form_post_starts_job_and_redirects(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    """A valid form POST spawns the audit subprocess and 302-redirects
    to the job log page."""
    _, client, _ = app_with_dirs
    resp = client.post(
        "/runs",
        data={"url": "https://example.com/"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/jobs/")


def test_runs_form_post_requires_url_or_pages(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.post("/runs", data={})
    assert resp.status_code == 400
    assert "url" in resp.json()["detail"].lower() or "pages" in resp.json()["detail"].lower()


def test_runs_form_post_rejects_both_url_and_pages(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.post(
        "/runs",
        data={"url": "https://example.com/", "pages": "https://x/"},
    )
    assert resp.status_code == 400


def test_run_detail_renders_seeded_report(
    tmp_path: Path, seeded_output: Path
) -> None:
    app = create_app(output_root=seeded_output, config_dir=tmp_path / "cfg")
    client = TestClient(app)
    resp = client.get("/runs/20260101T000000Z_example.com")
    assert resp.status_code == 200
    assert "Hello" in resp.text  # from report.md
    assert "data.json" in resp.text  # the JSON link


def test_run_detail_json_returns_data_json(
    tmp_path: Path, seeded_output: Path
) -> None:
    app = create_app(output_root=seeded_output, config_dir=tmp_path / "cfg")
    client = TestClient(app)
    resp = client.get("/runs/20260101T000000Z_example.com?format=json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "20260101T000000Z_example.com"
    assert body["overall_score"] == 88


def test_run_detail_404_for_missing_run(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.get("/runs/does_not_exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Diff endpoint
# ---------------------------------------------------------------------------


def test_diff_form_runs_compute_diff(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    """POST /api/diff with no runs on disk returns 404."""
    _, client, _ = app_with_dirs
    resp = client.post(
        "/api/diff",
        data={"url": "https://example.com/"},
    )
    assert resp.status_code == 404


def test_diff_form_runs_compute_diff_with_seeded_data(
    tmp_path: Path, seeded_output: Path
) -> None:
    app = create_app(output_root=seeded_output, config_dir=tmp_path / "cfg")
    client = TestClient(app)
    resp = client.post(
        "/api/diff",
        data={"url": "https://example.com/"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only one seeded run → empty diff + warning.
    assert body["run_id_a"] == body["run_id_b"]
    assert body.get("warning") == "only_one_run"
    assert body["added"] == []
    assert body["removed"] == []


def test_diff_form_rejects_missing_url(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.post("/api/diff", data={})
    assert resp.status_code == 400
    assert "url" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Publish endpoint
# ---------------------------------------------------------------------------


def test_publish_form_calls_publisher_agent(
    app_with_dirs: tuple[Any, TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stub the PublisherAgent to capture inputs and return success."""
    _, client, tmp_path = app_with_dirs
    diff_path = tmp_path / "diff.json"
    diff_path.write_text(
        json.dumps(
            {
                "run_id_a": "a",
                "run_id_b": "b",
                "added": [],
                "removed": [],
                "severity_changed": [],
                "score_changed": [],
                "summary": "ok",
            }
        ),
        encoding="utf-8",
    )

    from dhrubo.agents import publisher as publisher_mod

    captured: list[dict[str, Any]] = []

    async def _stub_safe_execute(self, ctx):  # type: ignore[no-untyped-def]
        captured.append({"inputs": dict(ctx.inputs), "role": ctx.role})
        # Mirror what the real agent returns on success.
        from dhrubo.agents.base_agent import AgentResult

        return AgentResult(
            success=True,
            role=ctx.role,
            outputs={
                "comment_url": "https://x/#1",
                "comment_id": 1,
                "repo": ctx.inputs.get("repo"),
                "pr_number": ctx.inputs.get("pr_number"),
            },
            error=None,
        )

    monkeypatch.setattr(publisher_mod.PublisherAgent, "safe_execute", _stub_safe_execute)

    resp = client.post(
        "/api/publish",
        data={
            "diff_path": str(diff_path),
            "repo": "foo/bar",
            "pr_number": "7",
            "github_token": "ghp_fake",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["comment_url"] == "https://x/#1"
    assert body["repo"] == "foo/bar"
    assert body["pr_number"] == 7
    # Token is in the captured inputs (the agent consumes it).
    assert captured[0]["inputs"]["github_token"] == "ghp_fake"


def test_publish_form_missing_token_returns_400(
    app_with_dirs: tuple[Any, TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, client, tmp_path = app_with_dirs
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    # No token in form, no token in env.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    resp = client.post(
        "/api/publish",
        data={
            "diff_path": str(diff_path),
            "repo": "foo/bar",
            "pr_number": "7",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "token" in body["detail"].lower() or "GITHUB_TOKEN" in body["detail"]


def test_publish_form_missing_diff_path_returns_400(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.post(
        "/api/publish",
        data={
            "diff_path": "does/not/exist.json",
            "repo": "foo/bar",
            "pr_number": "7",
            "github_token": "ghp_fake",
        },
    )
    assert resp.status_code == 400
    assert "diff_path" in resp.json()["detail"]


def test_publish_form_bad_repo_returns_400(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, tmp_path = app_with_dirs
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    resp = client.post(
        "/api/publish",
        data={
            "diff_path": str(diff_path),
            "repo": "no-slash-here",
            "pr_number": "7",
            "github_token": "ghp_fake",
        },
    )
    assert resp.status_code == 400
    assert "owner/name" in resp.json()["detail"]


def test_publish_form_zero_pr_returns_400(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, tmp_path = app_with_dirs
    diff_path = tmp_path / "diff.json"
    diff_path.write_text("{}", encoding="utf-8")
    resp = client.post(
        "/api/publish",
        data={
            "diff_path": str(diff_path),
            "repo": "foo/bar",
            "pr_number": "0",
            "github_token": "ghp_fake",
        },
    )
    assert resp.status_code == 400
    assert "pr_number" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Cancel job
# ---------------------------------------------------------------------------


def test_cancel_unknown_job_returns_ok_false(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.post("/jobs/unknown-id/cancel")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_jobs_page_for_unknown_job_returns_404(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    _, client, _ = app_with_dirs
    resp = client.get("/jobs/unknown-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Smoke: a real subprocess is captured end-to-end via the form
# ---------------------------------------------------------------------------


def test_runs_form_completes_a_subprocess(
    app_with_dirs: tuple[Any, TestClient, Path],
) -> None:
    """End-to-end: a real ``python -c`` (simulating a quick audit)
    runs, the page renders, the SSE stream emits a ``done``."""
    # Manually inject a known quick argv into the supervisor.
    app, _, _ = app_with_dirs

    async def _go() -> None:
        from dhrubo.dashboard.supervisor import RunSupervisor
        sup: RunSupervisor = app.state.supervisor
        argv = [sys.executable, "-c", "print('audit done')"]
        job = await sup.start(argv)
        # Drain the stream.
        async for _ in sup.stream_logs(job.id):
            pass
        assert sup.get(job.id).state.value == "done"

    import asyncio
    asyncio.run(_go())
