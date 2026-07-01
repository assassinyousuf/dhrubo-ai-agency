# Milestone 2 — Thin End-to-End Vertical Slice (Implementation Notes)

**Status:** ✅ Complete (July 2026)
**Scope:** Thin vertical slice — proves the entire framework shape with a
real working pipeline before adding the heavier browser / Lighthouse /
vision layers.
**Spec reference:** `docs/MILESTONE_2_SPEC.md` (the original proposed
spec, which we narrowed for M2).

## What was built

| Component | File | Purpose |
|---|---|---|
| `web_fetch` tool | `src/dhrubo/tools/web_fetch_tool.py` | httpx-backed HTTP fetch with timeout, redirect, content-type |
| `LLMAgent` base | `src/dhrubo/agents/llm_agent.py` | Declarative base: prompt template + response Pydantic model + JSON validation + retry |
| `PlannerAgent` | `src/dhrubo/agents/planner.py` | M2 deterministic; M4 will be LLM-backed |
| `WebsiteCrawlerAgent` | `src/dhrubo/agents/website_crawler.py` | Calls `web_fetch`, extracts title/meta/h1s/links via stdlib parser |
| `SeoReviewerAgent` | `src/dhrubo/agents/seo_reviewer.py` | First domain reviewer; produces structured `SeoReport` |
| `ReportWriterAgent` | `src/dhrubo/agents/report_writer.py` | Deterministic Markdown composer (no LLM) |
| `ExporterAgent` | `src/dhrubo/agents/exporter.py` | Writes `report.md` + `data.json` to a timestamped run directory |
| Pipeline (slim) | `src/dhrubo/workflows/website_audit_pipeline.py` | 5-task DAG: plan → crawl → seo_review → report → export |
| CLI wiring | `src/dhrubo/commands/cli.py` | `--output-dir`, env-driven LLM, friendly mock fallback |
| Mock LLM upgrade | `src/dhrubo/llm/mock_provider.py` | Returns SEO-shaped JSON when `response_format_json=True` so the pipeline runs end-to-end without an API key |
| Live integration test | `tests/test_integration.py` | Runs the full pipeline; skipped if `OPENAI_API_KEY` is absent |

## What it produces

```text
$ dhrubo run-audit --url https://example.com

Workflow website_audit finished with status completed.
Report written to output/20260701T144259Z_https___example.com/report.md
```

The produced `report.md` contains the page snapshot, an SEO score, the
list of issues sorted by severity, and a methodology footer.

## Architectural decisions worth recording

1. **Deterministic `PlannerAgent` for M2.** The plan is a static list of
   steps so the pipeline runs with no LLM. M4 graduates it to LLM-backed
   planning once we have a richer reviewer fleet for it to reason about.

2. **`LLMAgent` is the single most important abstraction in the
   framework.** Every future reviewer inherits from it. It handles Jinja2
   rendering, the LLM call, JSON-mode request, output schema validation,
   and a configurable retry loop. Adding a new reviewer is ~50 lines.

3. **Deterministic `ReportWriterAgent` for M2.** Report *facts* come
   from structured sub-reports. An LLM pass for narrative polish can be
   added later without touching the data.

4. **`MockProvider` returns schema-shaped JSON when asked** so offline
   runs are useful, not just for tests. The CLI tells the user "set
   OPENAI_API_KEY for real analysis" in the produced report.

5. **CLI `register_configured_exporter()`** demonstrates a pattern that
   will scale: agents that depend on runtime config (output dir, API
   base URL, etc.) get subclassed at boot and re-registered in the
   agent registry. The engine stays configuration-agnostic.

## Lessons learned (worth applying to future milestones)

- **Every Task must declare every input it reads in `input_keys`.** The
  engine builds `ctx.inputs` strictly from `task.input_keys`, not from
  `agent.input_keys`. M2 shipped with a bug where the planner's
  `target_url` wasn't propagated because the task's `input_keys` was
  empty. Fixed; codified in the test suite.
- **`__init_subclass__` cannot read `__abstractmethods__`** — that
  attribute is only populated *after* the subclass's body finishes. The
  `__abstract_base__` flag in `cls.__dict__` is the reliable way to
  opt out of registration for intermediate bases like `LLMAgent`.
- **`asyncio.run()` from inside a test is fine for short-lived agents.**
  The engine itself never calls `asyncio.run()` — only the CLI does.

## Verification

```bash
make install
make lint            # ruff — All checks passed
make typecheck       # mypy — 0 errors across 35 source files
make test            # 43 passed, 1 skipped (live integration)
make run-audit       # produces output/<timestamp>_.../report.md
```

## Recommended next: Milestone 3 (Browser Tool Foundation)

With the framework shape proven, the highest-leverage next step is the
**Playwright browser driver** behind a clean `BrowserDriver` interface:

- `tools/browser_driver.py` — abstract interface (`launch`, `goto`,
  `screenshot`, `evaluate`, `close`)
- `tools/playwright_impl.py` — Playwright adapter (gated behind
  `pip install dhrubo-ai-agency[browser]`)
- Replace `WebsiteCrawlerAgent` with a Playwright-backed version (keep
  the M2 one as a fast-fallback)
- Add a `ScreenshotAgent` that captures full-page + above-the-fold PNGs
- All covered by the same `LLMAgent` review pattern in M4+
