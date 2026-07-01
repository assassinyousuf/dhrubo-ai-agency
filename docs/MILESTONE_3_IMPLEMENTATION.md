# Milestone 3 — Browser Tool Foundation

> **Status:** complete · **Tests:** 55 passing (1 skipped — live LLM) · **Lint/Types:** clean

## What M3 delivered

A pluggable browser subsystem that lets the Website Audit Agent (a) render
JavaScript-heavy sites with Chromium, (b) capture multi-viewport screenshots,
and (c) gracefully fall back to HTTP/HTML when the browser is unavailable —
**all without making Playwright a hard dependency**.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/browser_driver.py` | Abstract `BrowserDriver` + `Viewport` / `Screenshot` / `PageSnapshot` dataclasses, `DEFAULT_VIEWPORTS` (desktop/mobile/tablet). |
| `src/dhrubo/tools/null_driver.py` | `NullDriver` returning a 1×1 PNG. Always available; useful for tests and for users who install without the `[browser]` extra. |
| `src/dhrubo/tools/playwright_impl.py` | `PlaywrightDriver` — headless Chromium via Playwright. Lazily loaded by the driver registry. |
| `src/dhrubo/tools/screenshot_tool.py` | `ScreenshotTool` — orchestrates the driver to capture all configured viewports. |
| `src/dhrubo/agents/screenshot_agent.py` | `ScreenshotAgent` — wires the tool into the agent system. Auto-promotes to Playwright when `DHRUBO_USE_REAL_BROWSER=1` and the driver is installed; falls back to null otherwise. |
| `src/dhrubo/core/retry.py` | `retry_async()` + `with_retry()` decorator with exponential backoff and jitter per `RetryConfig`. |

### M3 modifications

- `src/dhrubo/agents/website_crawler.py` — tries the browser path first when
  the env var is set and Playwright is installed, falls back to `WebFetchTool`
  on any failure. Stores `render_mode: "browser" | "http"` in
  `page_metadata`.
- `src/dhrubo/workflows/website_audit_pipeline.py` — adds a `screenshots` task
  in parallel with `seo_review`; both join into the `report` task.
- `src/dhrubo/agents/report_writer.py` — adds a "Screenshots" section listing
  viewport, dimensions, and file path.
- `pyproject.toml` — adds the `[browser]` optional extra
  (`playwright`); users still need `playwright install chromium` to fetch
  the browser binary.

### New tests (12 cases)

| File | Covers |
|---|---|
| `tests/test_browser.py` | `NullDriver` navigate + screenshot + a full `ScreenshotTool.safe_run` round-trip. |
| `tests/test_retry.py` | `retry_async` succeeds after N failures; exhausts attempts; propagates non-retriable errors; default policy sanity check. |
| `tests/test_screenshot_agent.py` | `ScreenshotAgent` writes files, fails cleanly when the URL is missing. |
| `tests/test_crawler_fallback.py` | `WebsiteCrawlerAgent` defaults to `render_mode="http"`; fails gracefully on missing URL. |

## Design decisions

### 1. Driver abstraction, not direct Playwright

The `BrowserDriver` ABC is the only thing tools and agents import. A driver
**registry** (`register_driver` / `get_driver`) lets us add Selenium, a remote
browser service, or an in-process CDP connection later without touching the
agent layer.

### 2. Lazy loading of Playwright

`null_driver.py` does a *runtime* `try/except ImportError` to import
`playwright_impl`. This means:

- The core install does **not** require Playwright.
- The CLI works out-of-the-box with `NullDriver` (1×1 PNG screenshots — fine
  for unit tests and CI).
- Setting `DHRUBO_USE_REAL_BROWSER=1` + `pip install dhrubo-ai-agency[browser]`
  unlocks real Chromium rendering with no code changes.

### 3. Fail-soft: never silently drop a site

Both `WebsiteCrawlerAgent` and `ScreenshotAgent` follow the same rule:

> If the preferred path (Playwright) fails, retry once with the HTTP/null
> fallback so the pipeline still produces a report. Log a `WARNING` so the
> operator can see the downgrade.

This is tested by `test_crawler_fallback.py` and exercised by
`test_screenshot_agent.py`.

### 4. Retry primitive, not decorator spaghetti

`core/retry.py` exposes two surfaces:

- `await retry_async(op, policy=..., op_name=..., retriable=...)` for inline
  use (where you want to capture context, e.g. inside an LLM agent).
- `@with_retry(policy=..., op_name=..., retriable=...)` for the common case.

Both honour `RetryConfig` (exponential base × multiplier, capped, with
optional uniform jitter) and never silently swallow non-retriable errors.

The LLM agent (M2) already has its own internal JSON-parse retry loop;
this is the higher-level **operation** retry for transient infra failures.

### 5. Viewports as a first-class concept

`Viewport`, `ViewportKind`, and `DEFAULT_VIEWPORTS` are shared between the
tool and the report writer, so the report and the screenshots can never
drift apart. The `Screenshots` section in `report.md` literally echoes the
viewport names and pixel dimensions.

## End-to-end smoke

```bash
# Mock LLM (no API key) + null driver (no Chromium)
python -m dhrubo.commands.cli run-audit --url https://example.com/
# → runs/<timestamp>_https___example.com_/report.md
```

The generated report includes:

- `Render mode: http` (since no real browser was requested)
- Three screenshot lines, one per `DEFAULT_VIEWPORTS` entry
- The mock SEO section (offline-friendly)

## What's intentionally NOT in M3

- **No real-browser CI test.** We don't ship a Playwright-driven
  integration test because the bundled browser binary is ~150 MB and most
  CI images don't have it. The interface is exercised against `NullDriver`
  and the Live driver is unit-callable for users who install the extra.
- **No persisted screenshot history across runs.** A single run = one
  screenshot set. Vector memory (M10) will let the agent reason about
  visual diffs across runs.
- **No JavaScript execution controls** (e.g. waiting for a selector,
  dismissing a cookie banner). Add via the `BrowserDriver.navigate`
  extension in a follow-up.

## Migration to M4 (UI Reviewer + Vision)

The browser foundation is now ready for the next milestone:

- A vision-capable LLM (Anthropic Claude with image input) can be added as
  a new `ILLMProvider` implementation.
- A new `UI Reviewer Agent` (`src/dhrubo/agents/ui_reviewer.py`) can read
  `screenshot_paths` from session memory, send the desktop PNG to the
  vision model, and emit a `UiReport` (score, summary, issues) shaped
  exactly like `SeoReport`.
- The pipeline gains one more node in parallel with `seo_review` —
  the report writer grows another section. No DAG engine changes needed.
