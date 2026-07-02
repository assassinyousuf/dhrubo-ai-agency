# Milestone 13 — Local web dashboard

> **Status:** complete · **Tests:** 61 M13 tests passing (1 env warning) · **Lint/Types:** clean

## What M13 delivered

After M10/M11 the audit is history-aware and after M12 it's
CI-integrated. But every interaction still required a terminal —
browsing a run meant `cat report.md`, kicking off a new audit
meant typing the same `dhrubo run-audit --url …` every time,
and posting a diff to a PR meant firing up the CLI again.

M13 adds a **local web dashboard** so a single user can browse
run history, drill into a report, trigger a new audit, and post
a diff to a PR — all in a browser. No new infrastructure: the
dashboard is a single FastAPI process on `127.0.0.1:8765`, talks
to disk and to the existing CLI, and ships in the already-defined
`[ui]` extra that has been a stub since the project's earliest
scaffold.

User-confirmed design choices:

1. **Local web dashboard, Jinja2 + vanilla JS, FastAPI server.**
   No React, no build step, no `node_modules`.
2. **Read + trigger + publish.** The UI exposes three actions:
   browse history, kick off a new `run-audit` subprocess,
   post a `diff.json` to a GitHub PR. No auth (single-user,
   loopback only).
3. **Subprocess pool + SSE.** A new run is launched by
   `asyncio.create_subprocess_exec(...)` of
   `python -m dhrubo.commands.cli run-audit …`; stdout streams
   to the browser via Server-Sent Events. Pool caps concurrent
   runs (default 2, configurable).
4. **Token in publish form (per-request).** The publish form
   accepts a GitHub token in the POST body; held only for the
   request lifetime, never persisted, cleared from the JS
   memory the moment the call returns.

The end-to-end user flow:

```text
$ pip install -e ".[ui]"
$ dhrubo dashboard --open
[2026-07-02 10:00:00] Starting local dashboard on http://127.0.0.1:8765
[2026-07-02 10:00:00] Output directory: ./output
[2026-07-02 10:00:00] Press Ctrl+C to stop.

# Browser opens. User clicks "New run", types a URL,
# watches the log stream live, gets redirected to the
# rendered report.md. Clicks "Diff this run vs prior",
# fills the publish form, posts to a PR.
```

## New components

### `src/dhrubo/dashboard/`

| Module | Role |
|---|---|
| `app.py` | `create_app(*, output_root, config_dir, settings=None) -> FastAPI`. App factory (no module-level instance) so tests can spin up isolated apps against `tmp_path`. Mounts `/static` (only if the static dir exists — wheel installs that bundle no assets won't 404), registers the four routers. |
| `paths.py` | Tiny helper module: `_resolve_static_dir()` and `_resolve_template_dir()`. Split out of `app.py` so the routes can import the path resolvers without forming a circular dependency (`app -> routes -> app`). |
| `supervisor.py` | `RunSupervisor` — asyncio-based process pool. One `Job` per audit-subprocess with state `queued | running | done | failed | cancelled`. `start(args)` spawns via `asyncio.create_subprocess_exec`, `stream_logs(id)` is an async generator that yields SSE-shaped dicts (`{"event": "stdout", "data": "…"}` / `{"event": "done", …}`). `cancel(id)` marks state as `cancelled` **before** calling `proc.terminate()` (Windows quirk: SIGTERM-mapped exit codes are positive and would otherwise be misclassified as `failed`). Pool cap: `start()` raises `PoolExhaustedError` when `N` concurrent jobs are already alive (default 2; configurable via `Settings.dashboard.max_concurrent_runs`). Buffers up to 5000 lines per job; late consumers see the full buffer replay. |
| `routes/system.py` | `GET /healthz` — `{ok, version, cwd, output_root, max_concurrent_runs, running_jobs}`. Liveness probe for CI scripts. |
| `routes/runs.py` | The dashboard's largest router. `GET /` (home: running jobs + recent runs), `GET /hosts/{seed_domain:path}` (per-host timeline), `GET /runs/{run_id}` (rendered `report.md` as HTML — uses `python-markdown`; `?format=json` returns the structured `data.json`), `POST /runs` (form action: builds the same `argv` shape the CLI's `run-audit` accepts, calls `supervisor.start(...)`, 302-redirects to `/jobs/{id}`), `GET /jobs/{job_id}` (page that subscribes to the SSE), `GET /jobs/{job_id}/events` (`EventSourceResponse` streaming `stdout` / `done` / `failed` / `cancelled` events), `POST /jobs/{job_id}/cancel` (terminate the running subprocess). |
| `routes/diff.py` | `GET /diff` (form) + `POST /api/diff` (JSON). Reuses `parse_window`, `select_runs_in_window`, and `compute_diff` directly — same code path the standalone `dhrubo diff` uses. When the window has only one run, returns an empty diff with a `warning: "only_one_run"` field. |
| `routes/publish.py` | `GET /publish` (form, with a `<datalist>` of recent `diff.json` paths for quick-pick) + `POST /api/publish` (JSON). Validates `diff_path` exists, `repo` is `owner/name` shape, `pr_number >= 1`, `github_token` (form field or `GITHUB_TOKEN` env), then calls `PublisherAgent` in-process. Token is deleted from local scope immediately after the call. |

### `src/dhrubo/dashboard/templates/`

Eight Jinja2 templates, all extending `base.html`:

- `base.html` — header (brand + Home/Diff/Publish/Health nav), footer, `{% block content %}`, `{% block scripts %}`.
- `home.html` — "New audit" form, "Running jobs" panel, "Recent runs" table (most-recent 25, sorted descending by `ts`, with a `<a>` per row to `/runs/{id}`).
- `host.html` — per-host timeline with cards (timestamp, target URL, n_pages, overall score, diff target).
- `report.html` — renders the `report.md` HTML body, with "View as JSON" + "Publish diff →" action buttons, plus a collapsible "Raw Markdown" `<details>`.
- `job.html` — page that opens an `EventSource('/jobs/{id}/events')`. Renders the job's argv + a "Cancel" form + a `<pre id="log">` element that the JS appends lines to.
- `diff.html` — diff form (URL + since + until) + a result panel that the JS fills in via XHR.
- `publish.html` — publish form (diff path datalist, repo, PR number, max issues, GitHub token) + a result panel. The token input has an explicit warning: "Never persisted. Cleared from memory after the call returns."
- `error.html` — friendly 4xx/5xx page.

### `src/dhrubo/dashboard/static/`

Four vanilla assets — no build step, no framework:

- `style.css` — dark-mode-friendly, CSS variables, no framework. State chips (running/done/failed/cancelled/queued), tables, forms, log `<pre>` with monospace + a contrasting dark background.
- `events.js` — `EventSource` consumer for the job log page. Subscribes to `stdout` / `done` / `failed` / `cancelled` events, appends each line to `<pre id="log">`, updates the state chip in the header on terminal events, disables the Cancel button.
- `diff.js` — POSTs the diff form to `/api/diff` and renders the JSON response (or error) in the result panel.
- `publish.js` — POSTs the publish form to `/api/publish` and clears the GitHub token input on response (the closure-scoped `token` variable goes out of scope at function exit).

### `tests/test_dashboard_*.py`

- `tests/test_dashboard_app.py` (7 tests) — factory returns `FastAPI`, all four routers are mounted, home renders with running jobs, health endpoint, factory accepts a non-existent output root, Jinja loader resolves all 8 bundled templates, static directory is present.
- `tests/test_dashboard_supervisor.py` (8 tests) — starts a subprocess and captures stdout, emits `done` on clean exit, emits `failed` on non-zero exit, `cancel()` terminates a long-running subprocess and marks it `cancelled`, pool cap rejects the N+1-th start, finished jobs remain in `list_jobs()`, separate jobs have separate `id`s, late consumers see the buffered lines replayed end-to-end.
- `tests/test_dashboard_routes.py` (18 tests) — home 200, form POST starts a job and 302-redirects to `/jobs/{id}`, form rejects empty + rejects both `--url` and `--pages`, run-detail renders seeded `report.md`, `?format=json` returns the structured payload, 404 for missing run, diff form returns 404 with no runs, returns the empty-diff+warning shape with one seeded run, 400 for missing URL, publish form calls the publisher and returns `{ok, comment_url, comment_id, repo, pr_number}`, missing token → 400, missing diff path → 400, bad repo shape → 400, zero PR number → 400, cancel unknown job returns `{ok: false}`, 404 for unknown job page, and an end-to-end smoke where a real `python -c` subprocess is captured by the supervisor and reaches `done`.
- `tests/test_cli.py` (4 new M13 tests) — `dashboard --help` lists every flag, missing `uvicorn` exits 2 with a helpful message, `--host` / `--port` flow into the uvicorn `Config`, no-flag invocation uses `Settings` defaults (loopback 8765).

## M13 modifications

- `pyproject.toml` — the existing `[ui]` extra gains
  `sse-starlette>=2.1` and `markdown>=3.6`. The `markdown` pin
  is so a user who installs only `[ui]` (skipping `[pdf]`) can
  still render `report.md` in the browser.
- `src/dhrubo/config/settings.py` — new `DashboardSettings`
  with `host: str = "127.0.0.1"`, `port: int = 8765`,
  `max_concurrent_runs: int = Field(default=2, ge=1, le=8)`,
  `start_browser: bool = False`. Wired onto `Settings` as
  `dashboard: DashboardSettings = Field(default_factory=…)`.
- `src/dhrubo/commands/cli.py` — new
  `@app.command("dashboard")` sibling of `run-audit` /
  `plan` / `diff` / `publish`. Loads `Settings`, then soft-
  imports `uvicorn` (so the dashboard subcommand doesn't drag
  in fastapi/uvicorn for users who only use the audit/diff/
  publish commands — the user gets a clean "Install with
  `pip install -e .[ui]`" error if they invoke `dashboard`
  without the extra). Optionally opens the default browser
  via a background thread on `--open`.

## CLI surface

### `dhrubo dashboard` (new)

```text
Usage: python -m dhrubo.commands.cli dashboard [OPTIONS]

  Start the local web dashboard (FastAPI + uvicorn).

  Browse run history, trigger new audits, diff two runs, post
  a diff.json to a PR. Loopback only; no authentication.

Options:
  --host TEXT             Bind address (default 127.0.0.1; loopback only).
  --port INTEGER          TCP port (default 8765).
  -o, --output-dir PATH   Run output directory (default ./output).
  -c, --config PATH       Config directory (default ./config).
  --open / --no-open      Open the dashboard URL in the default browser
                          on start. Off by default.
  --workers INTEGER       uvicorn workers (default 1; the supervisor is
                          in-process so workers > 1 is rarely useful).
  --reload / --no-reload  Auto-reload on Python file changes (dev only).
  --help                  Show this message and exit.
```

## Reused components

- **`load_run_index`** (`src/dhrubo/core/run_index.py:21`) —
  the dashboard's home + host pages call this verbatim.
- **`load_sub_reports_for_run`**
  (`src/dhrubo/core/run_index.py:41`) — for the run-detail
  page's data.json path.
- **`select_runs_in_window`**
  (`src/dhrubo/core/run_window.py:43`) + **`parse_window`**
  (`src/dhrubo/core/timeparse.py`) — same primitives the
  standalone `dhrubo diff` uses; the dashboard's diff page
  reuses them.
- **`compute_diff`** (`src/dhrubo/tools/diff_tool.py`) — re-
  exposed in M11 for the diff form's JSON endpoint.
- **`PublisherAgent`** (`src/dhrubo/agents/publisher.py`) —
  called in-process from `/api/publish`, not via subprocess;
  same code path the CLI uses.
- **`python-markdown`** — pulled into the `[ui]` extra so
  `report.md` rendering works without `[pdf]`. A known-good
  HTML pipeline (same as `MarkdownToPdfTool`).

## Implementation notes

- **App factory pattern (no module-level `app`).** The
  `create_app` factory builds a fresh `FastAPI` instance on
  every call, binds a single `RunSupervisor` to `app.state`,
  and mounts the routers. Tests inject `tmp_path` for both
  `output_root` and `config_dir` and never need to reset
  module state.

- **Circular import avoided via `paths.py`.** The dashboard's
  `app.py` imports each route module (to attach them), and
  the routes need `_resolve_template_dir()` to build the
  `Jinja2Templates` instance. Putting the resolver in `app.py`
  produced a `app -> routes -> app` cycle; moving it to
  `paths.py` broke the cycle cleanly.

- **Cancel state is set before terminate().** On Windows
  `proc.terminate()` produces a positive exit code (the OS
  doesn't have signals in the Unix sense), which would
  otherwise classify the job as `failed`. The supervisor
  flips `job.state = JobState.cancelled` **before** sending
  the terminate, so the `_await_exit` classifier (which
  short-circuits when state is already `cancelled`) keeps
  the right answer.

- **SSE event names match the JS.** The supervisor emits
  `{"event": "stdout", "data": "..."}` per line and a single
  terminal `done` / `failed` / `cancelled` event. The
  `events.js` consumer subscribes to those exact names, so
  there's no translation layer in the route.

- **Token handling.** The publish form sends the token in
  the POST body. The server reads it via FastAPI's `Form(...)`
  parameter, passes it to `PublisherAgent` via
  `AgentContext.inputs["github_token"]`, then `del token,
  github_token` before the response branch runs. The JS
  clears the input field and the closure-scoped `token`
  variable goes out of scope as soon as the fetch resolves.
  Nothing is logged, nothing is persisted, no cookies.

- **Pool cap as 503.** When the supervisor is at capacity,
  `start()` raises `PoolExhaustedError`; the runs route
  catches it and returns HTTP 503. The browser sees a clean
  error (rather than a hung form).

- **`--workers > 1` is a no-op in practice.** The supervisor
  lives in-process, so each uvicorn worker has its own pool.
  The user can crank it if they want, but they'll just
  double the cap. Documented in the help text.

## Out of scope for M13

- **Multi-user / authentication.** Single-user, loopback-only.
- **Subprocess pool across multiple machines.** All in one
  process.
- **Real-time diff while a run is in flight.** The dashboard
  shows the final `report.md` after a job completes;
  intermediate progress is just the log stream.
- **Editing run history / deleting rows manually.** The
  supervisor surfaces `cancellable`, not `deletable`. Use
  `rm -rf` for now.
- **GitLab MR publishing from the UI.** M12 is GitHub-only;
  the dashboard reuses the same `PublisherAgent`.
- **Templating user-defined pages.** The HTML is hard-coded.
- **WebSocket transport.** SSE only.
- **Production WSGI (gunicorn workers).** Uvicorn's
  `--workers` works but the in-process supervisor still caps
  to `max_concurrent_runs`; the first worker to receive a
  request owns the slot. Documented; not enforced.
- **Streaming PDF preview.** PDF is still generated post-hoc
  by the audit subprocess.

## Migration to M14

After M13 the user can `dhrubo dashboard`, browse, trigger,
diff, and publish from a single window. Candidate M14
directions:

- **GitLab MR support** in the publish form (parallel to
  M12 GitHub).
- **Auth + remote access** (cloud-hosted dashboard for
  multi-user teams).
- **Scheduled audits + cron UI** (turns M11's `--diff-since`
  into a UI control).
- **Run retention settings** (`Settings.output.retain_runs`
  becomes a UI toggle).
- **Tone-of-voice reviewer** (7th lens; the dashboard's
  per-lens breakdown automatically gains a row).
- **Custom alert rules** (e.g. "email me when
  security_report score drops below 70").
