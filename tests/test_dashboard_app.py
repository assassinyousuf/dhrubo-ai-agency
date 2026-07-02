"""`tests.test_dashboard_app` — App factory + smoke tests.

Covers: factory shape, router registration, health endpoint,
template loader resolution, and that the factory accepts
non-existent output directories (the supervisor creates them
on demand).
"""

from __future__ import annotations

from pathlib import Path

from dhrubo.dashboard.app import create_app
from dhrubo.dashboard.paths import (
    _resolve_static_dir,
    _resolve_template_dir,
)
from fastapi import FastAPI
from starlette.testclient import TestClient


def test_create_app_factory_returns_fastapi(tmp_path: Path) -> None:
    app = create_app(output_root=tmp_path, config_dir=tmp_path)
    assert isinstance(app, FastAPI)
    assert app.title == "Dhrubo Dashboard"


def test_app_includes_runs_routes(tmp_path: Path) -> None:
    """All four routers (system, runs, diff, publish) are mounted."""
    app = create_app(output_root=tmp_path, config_dir=tmp_path)
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    # /healthz is from system_router
    assert "/healthz" in paths
    # / from runs_router
    assert "/" in paths
    # /diff and /api/diff from diff_router
    assert "/diff" in paths
    assert "/api/diff" in paths
    # /publish and /api/publish from publish_router
    assert "/publish" in paths
    assert "/api/publish" in paths


def test_home_renders_with_running_jobs(tmp_path: Path) -> None:
    """GET / returns 200 + dashboard HTML even with no runs on disk."""
    app = create_app(output_root=tmp_path, config_dir=tmp_path)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dhrubo" in resp.text
    assert "New audit" in resp.text
    assert "Running jobs" in resp.text


def test_healthz_endpoint_ok(tmp_path: Path) -> None:
    app = create_app(output_root=tmp_path, config_dir=tmp_path)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body
    assert "cwd" in body
    assert "output_root" in body
    assert body["output_root"] == str(tmp_path)
    assert body["running_jobs"] == 0


def test_create_app_with_nonexistent_output_root_is_ok(tmp_path: Path) -> None:
    """Factory accepts a non-existent output_root; the supervisor
    doesn't touch disk until a run is started."""
    target = tmp_path / "does_not_exist_yet"
    app = create_app(output_root=target, config_dir=tmp_path)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["output_root"] == str(target)


def test_jinja_loader_picks_base_template(tmp_path: Path) -> None:
    """The Jinja loader resolves ``base.html`` from the bundled
    templates directory — a smoke check that the template path
    hasn't drifted away from the package."""
    templates_dir = _resolve_template_dir()
    assert (templates_dir / "base.html").exists()
    assert (templates_dir / "home.html").exists()
    assert (templates_dir / "job.html").exists()
    assert (templates_dir / "diff.html").exists()
    assert (templates_dir / "publish.html").exists()
    assert (templates_dir / "report.html").exists()
    assert (templates_dir / "host.html").exists()
    assert (templates_dir / "error.html").exists()


def test_static_dir_resolves(tmp_path: Path) -> None:
    """The static directory exists in the source tree and contains
    the three assets the templates reference."""
    static_dir = _resolve_static_dir()
    assert (static_dir / "style.css").exists()
    assert (static_dir / "events.js").exists()
    assert (static_dir / "diff.js").exists()
    assert (static_dir / "publish.js").exists()
