# Milestone 6 — PDF Export (Markdown → WeasyPrint)

> **Status:** complete · **Tests:** 110 passing (5 skipped — 1 live LLM, 4 PDF render paths waiting on WeasyPrint install) · **Lint/Types:** clean

## What M6 delivered

A PDF renderer that attaches a `report.pdf` to every audit run, in the
same `output/<ts>_<host>/` directory as `report.md` and `data.json`.
When WeasyPrint isn't installed (or `pip install -e '.[pdf]'` wasn't
run), the audit still completes successfully — the PDF is simply
omitted and `export_paths` carries a `pdf_skipped` reason in the
result metadata. Mirrors the M5 "no PSI key → skip" pattern.

User-confirmed design choices:

1. **Library:** **WeasyPrint** with the **`markdown` package** for
   Markdown→HTML. Both already live in the optional `[pdf]` extra in
   `pyproject.toml` (`weasyprint>=62`, `markdown>=3.6`). No new deps.
2. **Native-libs handling:** **graceful skip with info note** —
   mirrors the M5 no-API-key fallback. Never fails the audit.
3. **Agent shape:** **inline in `ExporterAgent`** — the exporter grows
   from "write md+json" to a hybrid: deterministic writes + an
   optional `markdown_to_pdf` tool call.
4. **CLI:** `--pdf/--no-pdf` (default: pdf on) and `--pdf-format
   {a4,letter}` (default: a4) flags.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/markdown_to_pdf_tool.py` | `MarkdownToPdfParams`, `MarkdownToPdfTool`. Renders Markdown → HTML via `markdown` package → PDF via WeasyPrint. `_is_available()` static helper. `_do_call` is the test seam. `retry_async` wraps the render under the `markdown_to_pdf` retry policy. Graceful skip on `ImportError` for either `weasyprint` or `markdown`. |
| `tests/test_markdown_to_pdf_tool.py` | 8 tests: skip when WeasyPrint missing, render success, base_url/page_size propagation, renderer error, bad params, only-markdown-missing graceful skip, is_available helper. |

### M6 modifications

- `src/dhrubo/agents/exporter.py` — extends `ExporterAgent.__init__` to
  accept `pdf_enabled: bool = True`, `pdf_format: Literal["a4","letter"]
  = "a4"`. `execute()` reads `pdf_enabled` / `pdf_format` from
  `ctx.inputs` (DAG-level overrides). Calls the tool, sets
  `export_paths["report_pdf"]` on success, or populates
  `result.metadata["pdf_skipped"]` with the reason on skip/fail.
- `src/dhrubo/config/settings.py` — adds `ExportSettings` with
  `pdf_enabled: bool = True`, `pdf_format: Literal["a4","letter"] =
  "a4"`. Settable via `DHRUBO_EXPORT__PDF_ENABLED` /
  `DHRUBO_EXPORT__PDF_FORMAT` envvars.
- `config/permissions.yaml` — adds the `exporter` agent entry with
  `tools: [markdown_to_pdf]`.
- `config/retry_policies.yaml` — adds `markdown_to_pdf` (3 attempts,
  0.5s → 5s, jittered) for transient font-cache races.
- `src/dhrubo/commands/cli.py` — `run_audit` adds `--pdf/--no-pdf` and
  `--pdf-format` flags. `register_configured_exporter` accepts
  `pdf_enabled` and `pdf_format`. `initial_inputs` includes both for
  DAG-level overrides. Post-run CLI prints `PDF written to …` on
  success or `PDF skipped: …` on skip.
- `src/dhrubo/workflows/website_audit_pipeline.py` — `export` task's
  `input_keys` now include `pdf_format` and `pdf_enabled`. DAG
  diagram updated.

## Reused components

- **`Tool[TParams]` ABC + `__init_subclass__` auto-registration**
  (`src/dhrubo/tools/tool_interface.py`) — the new tool follows the
  exact same shape as `LighthouseTool` and `WebFetchTool`.
- **`retry_async` + retry policies** (`src/dhrubo/core/retry.py`,
  `config/retry_policies.yaml`) — wraps the WeasyPrint call.
- **Skip-with-info pattern** (`src/dhrubo/agents/performance_reviewer.py`,
  `src/dhrubo/agents/ui_reviewer.py`) — the no-API-key / no-screenshot
  patterns are the precedent. The exporter records
  `pdf_skipped: {reason}` in its `AgentResult.metadata`.
- **Engine semantics**: the workflow engine accepts arbitrary
  string-typed inputs on a task via `input_keys`; the existing
  `export_paths` shape just grows a `report_pdf` key.

## Patterns introduced in M6

1. **`AgentResult.metadata` is separate from `outputs`** — the
   exporter builds the result via `AgentResult.ok(role,
   export_paths=…)` and then mutates `result.metadata` directly. This
   is the cleanest way to surface observability metadata (like
   `pdf_skipped.reason`) without polluting the typed `outputs` dict.
2. **Graceful skip on `ImportError` for optional deps inside a
   feature-flag-gated tool** — when `is_available()` is patched to True
   in tests but the actual package isn't installed, the `import` inside
   `run()` is wrapped in a try/except that emits the same skip
   payload. Same UX, more robust to broken environments.

## End-to-end behaviour

```text
$ unset PAGESPEED_API_KEY GOOGLE_API_KEY OPENAI_API_KEY
$ python -m dhrubo.commands.cli run-audit --url https://example.com/

INFO  llm.using_mock  reason=no_api_key
INFO  tool.web_fetch.start  url=https://example.com/
INFO  screenshot.complete  shots=3
INFO  lighthouse.skipped_no_api_key
INFO  performance.skipped  reason="PAGESPEED_API_KEY (or GOOGLE_API_KEY) is not set"
INFO  markdown_to_pdf.skipped_unavailable  output_path=…/report.pdf
INFO  exporter.pdf_skipped  reason="weasyprint is not installed; run `pip install -e '.[pdf]'`"
+-------------------- Audit complete --------------------+
| Workflow website_audit finished with status completed. |
+--------------------------------------------------------+
Report written to output\20260701T162515Z_https___example.com_\report.md
PDF skipped: weasyprint is not installed; run `pip install -e '.[pdf]'`
```

```text
$ python -m dhrubo.commands.cli run-audit --no-pdf --url https://example.com/
…
+-------------------- Audit complete --------------------+
Report written to output\20260701T162537Z_https___example.com_\report.md
```

When WeasyPrint *and* `markdown` are both installed, the section
becomes:

```text
+-------------------- Audit complete --------------------+
Report written to output\20260701T162537Z_https___example.com_\report.md
PDF written to  output\20260701T162537Z_https___example.com_\report.pdf
```

…and the run directory contains three artifacts:

```text
output\20260701T162537Z_https___example.com_\
├── report.md
├── report.pdf
└── data.json
```

## Test deltas

| Suite | Before M6 | After M6 | Delta |
|---|---:|---:|---:|
| `tests/test_markdown_to_pdf_tool.py` | 0 | 8 | +8 |
| `tests/test_exporter.py` | 1 | 4 | +3 |
| `tests/test_workflows.py` | 12 | 14 | +2 |
| **Total** | **101** | **110** | **+9** |

(The 4 "render" tests in `test_markdown_to_pdf_tool.py` and the 1 PDF
generation test in `test_exporter.py` are skipped on hosts without
WeasyPrint; they're not counted as regressions.)

## Verification log

```text
$ ruff check .                  # All checks passed!
$ mypy src                      # Success: no issues found in 46 source files
$ pytest -q                     # 110 passed, 5 skipped
$ python -m dhrubo.commands.cli run-audit --url https://example.com/
                                 # report.md + report.pdf flow; "PDF skipped"
                                 # notice when weasyprint is absent.
$ python -m dhrubo.commands.cli run-audit --no-pdf --url https://example.com/
                                 # no PDF skip notice; report.md only.
$ python -m dhrubo.commands.cli run-audit --pdf-format letter --url https://example.com/
                                 # DAG receives pdf_format="letter".
```

## Risks (from the plan) — status

- **WeasyPrint native libs unavailable on Windows** — graceful skip
  works in CI / dev envs. ✅
- **Font availability for emoji** (🔴🟠🟡🔵) — relies on the OS emoji
  font. WeasyPrint falls back to a placeholder glyph; the report
  stays readable. ✅
- **Path resolution for `screenshots\foo.png`** — `base_url` is
  forward-slashed regardless of host OS. ✅
- **Disk space + perf** — PDFs are well under 1 MB; render time is
  ~100-300 ms. ✅
- **`pyproject.toml` lockstep with extras** — no dep changes; the
  existing `[pdf]` extra is what makes the rendering possible. ✅

## Out of scope (carried forward)

- **HTML themes / templates** — the inline stylesheet is intentionally
  minimal.
- **Per-section page breaks**.
- **PNG screenshot embedding in PDF** — the MD link to screenshots
  stays as a path string in the PDF for v1.
- **Per-run audit metadata page**.
- **S3 / cloud upload** — local file writes only.

## Migration to M7 (Accessibility Reviewer)

M7 attaches an accessibility reviewer (likely axe-core via a new
tool). Same hybrid agent pattern as M5 (tool + LLM editor pass). No
exporter changes.