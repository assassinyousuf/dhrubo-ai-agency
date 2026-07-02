# Dhrubo AI Agency

Enterprise-grade autonomous AI Website Audit Agent platform.

> **Status:** v0.13 — Milestones 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, and 13 complete.
> - **M1 (Repository Foundation):** Core architecture, config, `BaseAgent`, `Tool` ABCs, asynchronous DAG workflow engine, LLM providers (OpenAI-compatible and mock), and CLI scaffold.
> - **M2 (LLM & Vertical Slice):** Real crawler, SEO reviewer, report writer, and exporter; pipeline runs end-to-end with LLM integration and JSON-parse retry loops.
> - **M3 (Browser Subsystem):** `BrowserDriver` interface, `NullDriver`, `PlaywrightDriver` (optional extra), `ScreenshotTool`, and `ScreenshotAgent`. The crawler auto-promotes to Playwright with HTTP fallback. Added exponential backoff retry middleware.
> - **M4 (UI Reviewer + Vision):** Multimodal `LLMMessage` contract (`ImageRef` + `images`), `image_utils` (stdlib), `UiReviewerAgent` that ingests all three viewport screenshots and emits a structured UI/UX sub-report. Report writer grew a `## UI Review` section.
> - **M5 (Performance Reviewer):** `LighthouseTool` calling PageSpeed Insights v5 over `httpx` with the `pagespeed_call` retry policy. `PerformanceReviewerAgent` (hybrid: deterministic tool call + LLM editor pass) emits a severity-rated performance sub-report. Skips gracefully with an `info` issue when `PAGESPEED_API_KEY`/`GOOGLE_API_KEY` isn't set. Report writer grew a `## Performance Review` section; methodology blurb bumped to v0.3 (SEO + UI + Performance).
> - **M6 (PDF Export):** `MarkdownToPdfTool` rendering the final Markdown to `report.pdf` via WeasyPrint + the `markdown` package (both in the `[pdf]` extra). `ExporterAgent` grew into a small hybrid: writes md+json, then calls the PDF tool. Skips gracefully with `pdf_skipped` metadata when WeasyPrint isn't installed. CLI adds `--pdf/--no-pdf` and `--pdf-format {a4,letter}` flags.
> - **M7 (Accessibility Reviewer):** `AxeTool` driving `axe-playwright-python` against a real headless Chromium (new `[a11y]` extra) emits a structured WCAG 2.0/2.1 violations payload. `AccessibilityReviewerAgent` (hybrid: axe → LLM editor pass) emits a severity-rated a11y sub-report with axe `impact` mapped 1:1 to the framework's severity vocabulary (critical→critical, serious→major, moderate→minor, minor→info). Skips gracefully with an `info` issue pointing at the missing `[a11y]` extra when axe/Playwright aren't importable. Report writer grew a `## Accessibility Review` section with a top-violations table; methodology blurb bumped to v0.4 (SEO + UI + Performance + Accessibility).
> - **M8 (Security + Branding Reviewers):** `SecurityTool` reusing the existing `WebFetchTool` (httpx GET) parses 8 known security headers (CSP, HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy, X-Content-Type-Options, COOP, CORP), HTTPS scheme, and `Set-Cookie` flags (Secure/HttpOnly/SameSite) — no new deps. `SecurityReviewerAgent` emits a severity-rated security sub-report (missing CSP → critical, missing HSTS on HTTPS → major, insecure cookies → major). `BrandingTool` reads `page_metadata` (logo, favicons, OG/Twitter image, theme color, social presence) + re-fetches the HTML to extract brand colors from inline `<style>` blocks via regex. `BrandingReviewerAgent` emits a severity-rated branding sub-report (no logo → major, low social presence → minor, title inconsistency → minor). The crawler was extended to surface favicon URLs and social-link presence on `page_metadata`. Report writer grew `## Security Review` and `## Branding Review` sections; methodology blurb bumped to v0.5 (SEO + UI + Performance + Accessibility + Security + Branding).
> - **M9 (Multi-page Audits):** `PageIndexerAgent` (deterministic — no LLM) resolves a list of URLs into a single canonical `pages: list[Page]` + `seed_domain: str`. The pipeline's DAG is now built dynamically per URL count via `build_website_audit_workflow(urls)` — single-URL (M8 task IDs verbatim, with a `page_indexer` prefix) and multi-URL (per-URL fan-out: `crawl_<i>`, `screenshots_<i>`, `seo_review_<i>`, ..., namespaced `page_<i>_<key>` outputs) shapes both share the same `report` aggregator + `export` task. CLI gained the `--pages <a,b,c>` flag (cap 25, mutually exclusive with `--url`); the run-dir slug now uses `seed_domain`; multi-page runs also write a `pages.json` index. Report writer grew a `## Summary` cross-page table + per-page H3 review sections; methodology blurb bumped to v0.6 (mentions "N pages").
> - **M10 (Comparison / Diff Runs):** The audit is now history-aware. Every issue carries a stable `id` (`slugify(title) + ":" + sha1(title|detail|severity)[:8]`) for diff identity. `DiffTool` is a pure-function tool that compares two sub-report payloads (single- or multi-page) over `id`-first identity with `(severity, title, detail)` fallback — emitting `added`, `removed`, `severity_changed`, `score_changed`, and a one-line `summary`. `DiffReviewerAgent` (deterministic — no LLM) calls `DiffTool` between `report` and `export` when `--diff-against <run_id>` is set. The exporter now writes a `runs/<ts>_<host>/index.json` row per run and embeds the structured `sub_reports` dict into `data.json` so a diff doesn't have to re-parse Markdown; `diff.json` is written alongside `data.json` when a diff was computed. The report writer grows a `## Diff vs <run_id>` H2 section (grouped per page for multi-page runs). CLI gained `--diff-against TEXT` which resolves the previous run's sub-reports via the per-host index and injects them as `previous_sub_reports` + `diff_against` into the initial inputs.
> - **M11 (Time-range Diffs + Scheduled Audits):** The audit is now scheduled-audit-aware. New `core/timeparse` module parses both relative (`7d`, `24h`, `1w`) and absolute (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SSZ`) time formats; new `core/run_window` module filters the per-host run index by `[start, end)`. `run-audit` gained `--diff-since TEXT` (mutually exclusive with `--diff-against`) which auto-resolves the earliest run in the window and funnels into the M10 path — turning any cron-driven `run-audit` into a scheduled audit that always shows what changed since the last run. New `@app.command("diff")` subcommand answers ad-hoc history queries (`dhrubo diff --url <host> --since 7d`); prints a per-lens human summary by default or writes `diff_<ts>_<host>.json` with `--json`. The M10 latent bug where the `## Diff vs <run_id>` section never appeared in live pipeline runs is fixed by moving the rendering to the exporter (the only task with both `final_report_md` and `diff_payload` in its inputs at the same time).
> - **M12 (CI Integration — GitHub PR Comments):** The audit is now CI-integrated. New `tools/markdown_diff_renderer.py` (`render_diff_comment`) is a pure-function renderer that turns a diff payload into a single Markdown body — H2 header + run-id sub-line + italicized summary + per-lens `+N / -M / Δscore` table + per-lens `<details>` blocks for added/removed issues (capped at 50 per lens via `--max-issues-per-lens`). New `tools/github_comment_tool.py` (`GitHubCommentTool`) wraps a single `httpx.AsyncClient.post` to `https://api.github.com/repos/<owner>/<repo>/issues/<n>/comments` with the new `github_post` retry policy (3 attempts, exponential backoff, retries 5xx + network errors only — 4xx short-circuits immediately). New `agents/publisher.py` (`PublisherAgent`, deterministic) reads a `diff_payload` from `ctx.inputs` (or loads from a `diff_path` on disk), renders, posts, emits `comment_url`. New `commands/cli.py` `@app.command("publish")` subcommand is a thin publisher primitive (no DAG) that resolves `--repo` / `--github-pr` / `GITHUB_TOKEN` from flags + env and prints the comment URL on success. New `config/permissions.yaml` `publisher` role with `github_comment` tool; new `retry_policies.yaml` `github_post` policy. README + docs bumped to v0.12.
> - **M13 (Local Web Dashboard):** A single-user web UI on `127.0.0.1:8765` for browse + trigger + publish. New `dhrubo.dashboard` package: `RunSupervisor` (asyncio process pool with SSE log streaming + `PoolExhaustedError` cap at `max_concurrent_runs`), `create_app()` FastAPI factory with no module-level globals (so tests can inject `tmp_path`), four routers (`system` for `/healthz`, `runs` for home/host/run-detail/job-log, `diff` for the diff form + JSON, `publish` for the diff-to-GitHub form + JSON). 8 Jinja2 templates + 4 vanilla JS/CSS assets (no build step, no framework). `PublisherAgent` is reused in-process — the token is held in the form body for the request lifetime and discarded immediately, never logged or persisted. `sse-starlette` and `markdown` are added to the existing `[ui]` extra (which had been a stub since the project's earliest scaffold). New `@app.command("dashboard")` soft-imports `uvicorn` (clean error if `[ui]` isn't installed) and supports `--host`, `--port`, `--open`, `--workers`, `--reload`. README + docs bumped to v0.13.
>
> See `docs/MILESTONE_1.md` through `docs/MILESTONE_13_IMPLEMENTATION.md` for specific milestone details. The full architecture is described in `dhrubo_architecture.md`.

## What This Is

Dhrubo AI Agency is a modular, multi-agent framework that runs an ecosystem of specialized AI agents. Rather than relying on a single monolithic prompt or agent, it breaks complex workflows into narrow, specific tasks (e.g., crawler, screenshot, UI reviewer, SEO reviewer, report writer). 

These agents collaborate through a standardized asynchronous **Directed Acyclic Graph (DAG) Workflow Engine**, abstracted tool layers, and stateful memory banks. The first complete capability is a full **Website Audit Agent**, but the framework is architected to scale seamlessly to dozens of other capabilities, including business consultancy and automated proposal generation.

## Key Features & Guiding Principles

- **Multi-Agent Orchestration**: Specialization over monoliths. Agents own single responsibilities (e.g., `SEO Reviewer`, `UI Reviewer`).
- **Tool Abstraction**: Agents never interact directly with dependencies like Playwright or Lighthouse. They use normalized `Tool` interfaces, ensuring maximum maintainability.
- **Pluggable Workflows**: The DAG engine supports wave-based scheduling and asynchronous agent execution without rigid procedural code.
- **Enterprise Resiliency**: Built-in exponential backoff retry middleware (`core/retry.py`) and fail-soft fallbacks (e.g., falling back to HTTP if the Playwright browser crashes).
- **Configurable**: Model routing (picking cheap vs. smart models for specific tasks), retry policies, and permissions live in YAML configurations.
- **Observable**: Structured JSON logging and tracing interfaces are built in from day one.

## Install

```bash
# Core only (no heavy browser binaries)
pip install -e .

# With browser automation support (Playwright)
pip install -e ".[browser]"

# With accessibility (axe-core) auditing on top of Playwright
pip install -e ".[a11y]"

# Everything you might want for local development (Browser, A11y, PDF, RAG, Vision)
pip install -e ".[browser,a11y,pdf,anthropic,dev]"

# If using browser features, install the Chromium binary
playwright install chromium
```

## CLI Usage

```bash
# General help
dhrubo --help
dhrubo run-audit --help

# End-to-end audit (mock LLM if no OPENAI_API_KEY is set, uses HTTP fallback)
dhrubo run-audit --url https://example.com/

# Run with a real browser for JavaScript-rendered pages and multi-viewport screenshots
DHRUBO_USE_REAL_BROWSER=1 dhrubo run-audit --url https://example.com/

# Run a real performance review (requires a PageSpeed API key)
PAGESPEED_API_KEY=... OPENAI_API_KEY=sk-... dhrubo run-audit --url https://example.com/
# (GOOGLE_API_KEY is also accepted as the PageSpeed key.)

# Disable PDF export (default is on; PDF generation needs `pip install -e .[pdf]`)
dhrubo run-audit --no-pdf --url https://example.com/

# Render the PDF at letter size instead of A4
dhrubo run-audit --pdf-format letter --url https://example.com/

# Enable real axe-core accessibility auditing (needs `pip install -e ".[a11y]"` and `playwright install chromium`)
dhrubo run-audit --url https://example.com/
# Without [a11y]: "## Accessibility Review" shows "n/a (Accessibility review skipped)" with one info issue.

# M12: ad-hoc history query — diff the earliest vs latest run in a 7-day window
dhrubo diff --url https://example.com/ --since 7d
# Or write a diff.json for downstream publishing
dhrubo diff --url https://example.com/ --since 7d --json

# M12: post a diff.json to a GitHub PR as a Markdown comment
# (requires the GITHUB_TOKEN env var; --repo falls back to GITHUB_REPOSITORY)
GITHUB_TOKEN=ghp_... dhrubo publish \
  --diff-path output/<ts>_example.com/diff.json \
  --repo octocat/Hello-World --github-pr 42
# → "Comment posted: https://github.com/octocat/Hello-World/pull/42#issuecomment-<id>"

# M13: launch the local web dashboard (needs `pip install -e ".[ui]"`)
pip install -e ".[ui]"
dhrubo dashboard --open
# → http://127.0.0.1:8765 — browse runs, trigger new audits, post diffs to PRs
```

## Development

```bash
make install   # editable install + dev extras
make lint      # ruff + mypy (strict mode)
make test      # pytest suite
make run-audit # runs against a stub target (no real LLM yet)
```

### Outputs

The audit pipeline produces artifacts under `runs/<timestamp>_<host>/`. For example:
- `report.md` (Human-readable technical audit, including **Screenshots**, **SEO Review**, **UI Review**, **Performance Review** sections).
- `report.pdf` (A4 / letter PDF rendering of the markdown; requires `pip install -e .[pdf]`. Skipped gracefully when WeasyPrint isn't installed.)
- `data.json` (Structured raw data).

## Repository Layout

```text
dhrubo-ai-agency/
├── agents/      # Specialized agent implementations (Planner, Coordinator, Reviewers)
├── commands/    # CLI entry points (Typer/Click)
├── config/      # YAML configurations (models, retry policies, permissions, logging)
├── core/        # Shared utilities, custom errors, structured logging, tracing
├── docs/        # Milestone tracking and extended implementation documentation
├── memory/      # State management (Session, Task, Vector Memory)
├── pipelines/   # Higher-level, cross-cutting pipelines
├── prompts/     # Version-controlled Jinja2 prompt templates
├── templates/   # Output rendering templates (HTML, MD, cold emails, proposals)
├── tools/       # Abstract + concrete tool implementations (Browser, Lighthouse)
├── workflows/   # Task orchestration (DAG Engine, Task Queue)
└── tests/       # Pytest test suite ensuring architectural contracts
```

See `dhrubo_architecture.md` (one level up) for the full system architecture rationale.
