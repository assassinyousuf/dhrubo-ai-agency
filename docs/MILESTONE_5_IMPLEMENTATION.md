# Milestone 5 — Performance Reviewer (PageSpeed Insights)

> **Status:** complete · **Tests:** 101 passing (1 skipped — live LLM) · **Lint/Types:** clean

## What M5 delivered

A hybrid Performance reviewer that calls Google's public **PageSpeed
Insights v5** endpoint over HTTPS, parses the Lighthouse JSON payload, and
runs an LLM editor pass that turns raw metrics + opportunities into a
**severity-rated `issues[]` sub-report**. When no PSI API key is
configured, the reviewer short-circuits with a fully-shaped skip payload
(`score=None` + one `info` issue) — exactly mirroring the M4
no-screenshot UX.

The audit report now covers **SEO, UI (vision-based), and
Performance** — the first three sub-reviewers in the v0.3 lineup.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/lighthouse_tool.py` | `LighthouseParams`, `LighthouseTool`. Calls PSI v5 over `httpx`, wrapped in `retry_async` with the `pagespeed_call` policy. `_do_call` is the test seam. `_summarize()` extracts score, metrics, opportunities, CrUX field-data bit, and a trimmed `raw` block (≤8 KB) for the prompt. |
| `src/dhrubo/agents/performance_reviewer.py` | `PerformanceIssue`, `PerformanceMetric`, `PerformanceOpportunity`, `PerformanceReport`. `_NO_PERF_DATA_REPORT` constant for the skip case. `PerformanceReviewerAgent(LLMAgent)` — hybrid execute() (tool-first, LLM second). |
| `tests/test_lighthouse_tool.py` | 9 tests: skip-without-key, success path, 4xx, transport error, non-JSON, has_api_key helper, strategy passthrough, GOOGLE_API_KEY fallback. |
| `tests/test_performance_reviewer.py` | 8 tests: skip-without-key (LLM **never** called), missing URL, happy path with LLM, retry on invalid JSON, retry on schema fail, missing LLM, schema sanity. |

### M5 modifications

- `src/dhrubo/agents/__init__.py` — registers `PerformanceReviewerAgent`
  (and the `PerformanceIssue` / `PerformanceReport` types).
- `src/dhrubo/agents/report_writer.py` — adds `## Performance Review`
  section between UI and Methodology; renders `score` (with the
  `n/a (Performance review skipped)` placeholder when `None`),
  `summary`, strategy, CrUX field-data bit, top metrics table, severity
  issues. Methodology blurb updated to v0.3 (SEO + UI + Performance).
  `report_metadata` extended.
- `src/dhrubo/workflows/website_audit_pipeline.py` — adds the
  `perf_review` task depending on `crawl`; the `report` task now waits
  on `screenshots`, `seo_review`, `ui_review`, **and** `perf_review`.
- `config/retry_policies.yaml` — adds the `pagespeed_call` policy
  (4 attempts, 2s → 30s exponential, jittered).
- `pyproject.toml` — drops the redundant `lighthouse>=6.0` line from
  the `performance` extra (it pinned the npm CLI which we don't use;
  PSI is HTTP-only). The `performance` extra is reserved for a future
  local-Lighthouse integration.
- `src/dhrubo/llm/mock_provider.py` — adds a `_FALLBACK_PERF` shape and
  a `performance` / `pagespeed` / `lighthouse` / `core web vitals` /
  `psi ` / `lcp` / `cls` keyword branch.
- `src/dhrubo/core/retry.py` — adds `__all__` so `RetryConfig`,
  `retry_async`, `with_retry`, and `DEFAULT_RETRY` are importable as a
  public surface.

## Reused components

- `LLMAgent` (`src/dhrubo/agents/llm_agent.py`) — supplies prompt
  rendering, JSON-mode request, Pydantic validation, retry loop.
- `Tool[TParams]` ABC + `__init_subclass__` auto-registration
  (`src/dhrubo/tools/tool_interface.py`) — the tool follows the same
  shape as `WebFetchTool`.
- `WebFetchTool` recipe (`src/dhrubo/tools/web_fetch_tool.py`) —
  `httpx.AsyncClient`, `try/except httpx.HTTPError → ToolError`.
- `retry_async` + `retry_policies.yaml` (`src/dhrubo/core/retry.py`) —
  wraps the PSI call.
- `_NO_SCREENSHOT_REPORT` pattern (`src/dhrubo/agents/ui_reviewer.py`)
  — defines `_NO_PERF_DATA_REPORT` for the skip case.
- `_severity_badge` + `_SEVERITY_ORDER`
  (`src/dhrubo/agents/report_writer.py`) — reused for the new section.
- `ConfigError` / `ToolError` / `AgentError`
  (`src/dhrubo/core/errors.py`) — used for the right failures.
  **Missing API key is not an error.**

## Patterns introduced in M5

1. **`ctx.metadata` side-channel** — `AgentContext` is a slotted
   dataclass, so subclasses can't stash auxiliary data on `self`
   attributes from inside `execute()`. The reviewer writes the PSI
   payload to `ctx.metadata["_psi_payload"]` and reads it back from
   `build_variables()`. This is now the canonical pattern for hybrid
   agents in the framework.
2. **`_do_call` test seam** — `LighthouseTool` exposes the raw HTTP
   call as an overridable instance method so tests can monkey-patch it
   without pulling in `respx`. Same shape as `WebFetchTool`.
3. **Back-fill from source of truth** — the LLM response model defaults
   `metrics` and `opportunities` to `[]`, so `setdefault` won't help.
   The agent always overwrites these two fields from the PSI payload
   after the LLM call returns. This was a real M4 bug fix carried
   forward.

## End-to-end behaviour

```text
$ unset PAGESPEED_API_KEY GOOGLE_API_KEY OPENAI_API_KEY
$ python -m dhrubo.commands.cli run-audit --url https://example.com/

INFO  llm.using_mock  reason=no_api_key
INFO  tool.web_fetch.start  url=https://example.com/
INFO  screenshot.complete  shots=3
INFO  lighthouse.skipped_no_api_key  url=https://example.com/  requester=performance_reviewer
INFO  performance.skipped  reason="PAGESPEED_API_KEY (or GOOGLE_API_KEY) is not set"
+-------------------- Audit complete --------------------+
| Workflow website_audit finished with status completed. |
+--------------------------------------------------------+
```

The rendered `report.md` includes the new section:

```markdown
## Performance Review

**Score:** n/a (Performance review skipped)
**Summary:** Performance review skipped — no PageSpeed API key was configured.
_CrUX field data available:_ no

### 🔵 Info — Performance review not run

- **Finding:** The Lighthouse tool did not call PageSpeed Insights
  because neither PAGESPEED_API_KEY nor GOOGLE_API_KEY is set.
- **Recommendation:** Set PAGESPEED_API_KEY (or GOOGLE_API_KEY) in the
  environment and re-run to enable performance auditing.
```

When `PAGESPEED_API_KEY` (or `GOOGLE_API_KEY`) **and**
`OPENAI_API_KEY` are both set, the section renders a real score, top
metrics (LCP / FCP / TBT / CLS), the largest opportunities, and
severity-rated `issues`.

## Test deltas

| Suite | Before M5 | After M5 | Delta |
|---|---:|---:|---:|
| `tests/test_lighthouse_tool.py` | 0 | 9 | +9 |
| `tests/test_performance_reviewer.py` | 0 | 8 | +8 |
| `tests/test_workflows.py` (DAG) | 9 | 12 | +3 |
| **Total** | **81** | **101** | **+20** |

## Verification log

```text
$ ruff check .                     # All checks passed!
$ mypy src                         # Success: no issues found in 45 source files
$ pytest -q                        # 101 passed, 1 skipped
$ python -m dhrubo.commands.cli run-audit --url https://example.com/
                                    # report.md contains ## Performance Review
                                    # + Methodology v0.3
```

## Risks (from the plan) — status

- **Rate limits** — `pagespeed_call` retry policy + skip-without-key
  fallback. ✅
- **Latency** — 60s timeout in `LighthouseParams`. ✅
- **Cost** — `max_tokens: 2048` default, trimmed PSI summary (≤8 KB).
  ✅
- **Schema drift** — PSI JSON parsed by ID allow-list, safe defaults
  for missing keys. ✅
- **httpx mocking** — `monkeypatch(tool._do_call, lambda: ...)` seam
  works for now; `respx` is a follow-up. ✅

## Out of scope (carried forward)

- Local Lighthouse CLI integration (the `performance` extra).
- Field-data-only reports.
- Cross-run regression tracking (needs M10 vector memory).
- Multi-strategy averaging (mobile + desktop in parallel).
- `respx` httpx mocking library.

## Migration to M6 (PDF export via WeasyPrint)

M6 attaches a PDF rendering of the final Markdown report. No engine or
agent changes; just a new tool (`MarkdownToPdfTool`) and an exporter
extension.
