# Milestone 7 — Accessibility Reviewer (axe-core via Playwright)

> **Status:** complete · **Tests:** 137 passing (5 environment-skipped) · **Lint/Types:** clean

## What M7 delivered

A fourth review lens — **Accessibility** — built on **axe-core**, the
de-facto industry accessibility engine. The audit now reports WCAG
2.0/2.1 violations on a real browser pass (headless Chromium via
Playwright), then asks an LLM to triage and explain them in plain
English. Without the optional `[a11y]` extra, the audit still
completes — the section renders as `n/a (Accessibility review
skipped)` with one info issue pointing at the missing extras.

User-confirmed design choices:

1. **Library:** `axe-playwright-python` (depends on Playwright,
   already in the `[browser]` extra). New `[a11y]` extra in
   `pyproject.toml` lists both `playwright` and `axe-playwright-python`
   so a single `pip install -e ".[a11y]"` ships a working a11y stack.
2. **Driver seam:** `AxeTool` constructs `PlaywrightDriver` directly
   when the `[a11y]` extras are installed, mirroring how
   `screenshot_tool.py` already does. No changes to the abstract
   `BrowserDriver` interface — the axe tool needs page-handle access
   for `axe.run()`, and the existing abstraction doesn't expose one.
3. **Severity mapping:** axe `impact` 1:1 → `critical/major/minor/info`.
   The LLM confirms / refines with reasoning in the editor pass.
4. **Skip behavior:** graceful skip with `_NO_A11Y_DATA_REPORT`
   (`score=None` + one `info` issue pointing at the missing `[a11y]`
   extra) when neither `playwright` nor `axe-playwright-python` is
   importable. Audit never fails. Mirrors the M5 / M6 skip patterns.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/axe_tool.py` | `AxeParams`, `AxeTool` calling `axe-playwright-python` over `PlaywrightDriver`. `_do_call` is the test seam. `is_available()` static helper checks both `playwright` and `axe_playwright_python`. The shared runner `_run(tool, params, ctx)` is module-level; `AxeTool.run` delegates to it. Skip payload returned when `is_available()` is False or any exception fires (graceful skip). `retry_async` wraps the run under the new `axe_scan` policy. Normalizer flattens axe violations into a compact shape (id / impact / severity / help / help_url / tags / nodes_count / sample_target / sample_html). Prompt helper `format_violations_for_prompt` renders a cap-25 bullet list. |
| `src/dhrubo/agents/accessibility_reviewer.py` | `AccessibilityIssue`, `AccessibilityReport`, `_NO_A11Y_DATA_REPORT` constant, `AccessibilityReviewerAgent(LLMAgent)`. Hybrid shape (tool → LLM editor pass). Uses `ctx.metadata["_a11y_payload"]` side-channel to pass the axe payload from `execute()` to `build_variables()`. Back-fills `violations_count` / `tags_run` / `viewport` / `final_url` / `fetched_at` / `skipped` from the tool payload so the LLM can't accidentally blank them. |
| `tests/test_axe_tool.py` | 12 tests: skip when Playwright missing, skip when axe-playwright missing (graceful), happy path with canned violations, severity mapping, normalize helpers (empty, drop per-node HTML, prompt cap, prompt empty), URL validation, navigation error graceful skip, retry on transient error, `is_available` returns bool. |
| `tests/test_accessibility_reviewer.py` | 11 tests: skip when no browser, skip when missing target_url, skip when tool returns error, happy path with back-fill, retry on invalid JSON, retry on schema fail, missing-LLM-when-data-available, severity mapping (axe impact → framework severity), prompt helper embeds severity tags, schema sanity (score optional, rejects bad severity, rejects bad score). |

### M7 modifications

- `src/dhrubo/agents/__init__.py` — registers
  `AccessibilityReviewerAgent`, `AccessibilityReport`,
  `AccessibilityIssue` in the agent registry.
- `src/dhrubo/llm/mock_provider.py` — adds `_FALLBACK_A11Y` constant
  (score=60, mock info issue, `violations_count=0`,
  `tags_run=[wcag2a,wcag2aa,wcag21a,wcag21aa]`, `viewport="desktop"`,
  `skipped=False`). Keyword branch in `_fallback_for` slotted **before**
  the perf branch — matches `accessibility`, `wcag`, `axe`, `aria`,
  `contrast`, `color contrast`, `alt text`, `screen reader`.
- `src/dhrubo/agents/report_writer.py` — `input_keys` extended with
  `"a11y_report"`. New `## Accessibility Review` section between
  Performance Review and Methodology. Renders score (`n/a
  (Accessibility review skipped)` placeholder when None), summary,
  viewport, WCAG tags run, violations count, top violations table
  (cap 10) with columns `| Rule | Impact | Severity | Nodes | Help |`,
  severity-rated issues, methodology blurb → v0.4 ("SEO, UI,
  Performance, Accessibility"). `report_metadata["sections"]` adds
  `"accessibility"`; `["sub_reports"]` adds `"a11y_report"`.
- `src/dhrubo/workflows/website_audit_pipeline.py` — adds
  `a11y_review` task (`role="accessibility_reviewer"`,
  `depends_on=["screenshots"]`,
  `input_keys=("target_url", "page_metadata")`,
  `output_keys=("a11y_report",)`). `report.depends_on` adds
  `"a11y_review"`; `report.input_keys` adds `"a11y_report"`. Docstring
  DAG diagram updated.
- `config/permissions.yaml` — adds `accessibility_reviewer` with
  `tools: [axe]`.
- `config/retry_policies.yaml` — adds `axe_scan` (3 attempts, 1.0s →
  10s, jittered) for browser-cold-start transient failures.
- `pyproject.toml` — adds `[a11y]` extra:
  `playwright>=1.45`, `axe-playwright-python>=0.1`. Re-lists
  `playwright` so the extra is independently usable without the
  screenshot pipeline (a doc comment explains why).
- `tests/test_workflows.py` — adds 3 DAG tests
  (`test_a11y_review_node_in_dag`, `test_report_waits_on_a11y_review`,
  `test_workflow_validates_after_m7`).

## Reused components

- **`AccessibilityReviewerAgent(LLMAgent)`** — inherits prompt
  rendering, JSON-mode request, Pydantic validation, the retry loop.
  Mirrors `PerformanceReviewerAgent`'s hybrid `execute()` (tool first,
  LLM second; skip-payload when the tool returns `skipped=True`).
- **`ctx.metadata` side-channel** — same pattern as M5: stash the
  axe payload on `ctx.metadata["_a11y_payload"]` so `build_variables()`
  can read it back.
- **`_NO_*_REPORT` constant** — the `_NO_PERF_DATA_REPORT` /
  `_NO_SCREENSHOT_REPORT` precedent.
- **`AxeTool`** follows `LighthouseTool`'s pattern (Pydantic params,
  `ToolResult.ok(... skipped=True)` on unavailability, retry policy,
  `_do_call` seam).
- **`retry_async` + retry policies** (`src/dhrubo/core/retry.py`,
  `config/retry_policies.yaml`) — wraps the axe run under the new
  `axe_scan` policy.
- **`PlaywrightDriver`** — the tool reuses it directly, same pattern
  as `screenshot_tool.py`.
- **`_severity_badge` + `_SEVERITY_ORDER`** (`report_writer.py`) —
  reused for the new section.

## Patterns introduced in M7

1. **`AxeTool.run` as a thin delegating method on the class with a
   module-level shared body** — `AxeTool.run(self, params, ctx)` calls
   `_run(self, params, ctx)`. This satisfies Python's ABC
   abstract-method check (which freezes `__abstractmethods__` at class
   body finalization, before any post-module assignment) while
   preserving the test seam: tests can call `AxeTool.run` directly
   on a stubbed instance with a no-op retry policy.
2. **Severity mapping stays 1:1 at the tool layer** — axe `impact`
   maps to the framework's severity vocabulary inside
   `_normalize_violation`. The LLM confirms / refines wording but
   doesn't get to invent new rules; the rubric stays consistent
   across reviewers (Performance / UI / Accessibility all share the
   same severity scale).
3. **Top-violations prompt cap (25)** — axe-core has 100+ rules.
   Even `wcag2a`/`aa` typically surfaces <30; the cap protects the
   LLM token budget. The full raw payload is preserved in the
   agent's metadata for debugging.

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
ruff check .         # clean
mypy src             # clean (48 source files)
pytest -q            # 137 passed, 5 environment-skipped (4 PDF, 1 live LLM)

# End-to-end without [a11y]
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
# → "## Accessibility Review" with score "n/a (Accessibility review
#   skipped)", summary, one info issue pointing at the missing [a11y] extra.

# End-to-end with axe installed
pip install -e ".[a11y]"
playwright install chromium  # one-time
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
# → "## Accessibility Review" with real axe violations table + LLM-graded issues.
```

## Risks

- **axe-playwright-python stability** — version `>=0.1` is a newish
  wrapper. If it proves flaky, fall back to vendoring axe.min.js and
  using `add_init_script` (see M8 migration notes).
- **Browser-cold-start overhead** — `axe.run()` is ~1-3s per scan,
  plus Playwright cold start ~3-5s. Total adds ~10s to the audit.
  Worth it for the value.
- **Tag overload** — axe-core has 100+ rules; we run
  `wcag2a/aa` + `wcag21a/aa` by default. The 25-violation prompt cap
  protects the LLM token budget.
- **Privacy / headed mode** — axe runs headless; no PII concerns.

## Out of scope for M7

- **Inline-result PDF embedding** — `axe violations[]` JSON is too
  large to embed; summary table only.
- **Per-rule explanations** beyond axe's own help text — the LLM does
  that.
- **Differential scanning** (compare runs) — vector memory (M10).
- **Multiple URLs** — single page per audit.
- **CI integration** — axe-core itself, no GitHub Actions work here.

## Migration to M8 (Security / Branding / Per-rule-deep-dives)

M8 will add a security reviewer (mixed-content, CSP headers, etc.)
following the same hybrid pattern as M5/M7. No exporter / report-
writer changes — just a new reviewer and a new `## Security Review`
section between Accessibility and Methodology.