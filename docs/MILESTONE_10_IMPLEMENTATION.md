# Milestone 10 — Comparison / diff runs

> **Status:** complete · **Tests:** 244 passing (5 environment-skipped) · **Lint/Types:** clean

## What M10 delivered

The audit is now **history-aware**. After M9 every run covers
N pages × 6 lenses in one go, but each run was isolated. M10
add three primitives that turn the pipeline into a recurring
audit tool:

1. **Run history index.** Every export appends one row to
   `runs/<ts>_<host>/index.json` so prior runs are discoverable
   by `--diff-against <run_id>`.
2. **Structured sub-reports on disk.** `data.json` grows a
   `sub_reports` key (single- or multi-page) holding the raw
   per-lens payloads so a diff doesn't have to re-parse Markdown.
3. **`--diff-against <run_id>` CLI flag.** When set, the DAG
   inserts a `diff` task between `report` and `export`. The
   report writer grows a `## Diff vs <run_id>` H2 section listing
   added / removed / severity-changed / score-changed issues per
   lens (and per page, for multi-page runs).

User-confirmed design choices:

1. **Run index layout.** Per-`<host>` `runs/<ts>_<host>/index.json`
   — each row contains `run_id, ts, target_url(s), seed_domain,
   n_pages, sub_reports_path, pages_json_path, diff_against`. New
   runs append. `--diff-against <run_id>` resolves by walking
   every per-host index. (`runs/_index.json` was the alternative;
   per-host keeps the file small.)
2. **Sub-reports on disk.** Added to `data.json` under a new
   `sub_reports` key — same file as today. Single-page: dict
   with the 6 lens payloads. Multi-page: dict with namespaced
   `page_<i>_<key>` payloads (same shape the report writer
   already reads from `ctx.inputs`).
3. **Diff render location.** New top-level `## Diff vs <run_id>`
   H2 section in `report.md`, before `## Page Snapshot` /
   `## Summary`. Only emitted when `--diff-against` is set.
4. **Stable issue IDs.** Every issue now carries an `id` field
   computed as `slugify(title) + ":" + sha1(title|detail|severity)[:8]`.
   Identity for diffing is `id` first, fallback
   `(severity, title, detail)`.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/core/issue_id.py` | `compute_issue_id(title, detail, severity)` and `populate_issue_ids(payload)`. Walks both flat and multi-page payload shapes; back-fills `id` on every issue. |
| `src/dhrubo/tools/diff_tool.py` | `DiffParams(run_id_a, run_id_b, sub_reports_a, sub_reports_b)`, `DiffTool._do_call`. Pure-function diff: id-first identity, multi-page support, score diff, summary line. |
| `src/dhrubo/agents/diff_reviewer.py` | `DiffReviewerAgent(BaseAgent)` — deterministic (no LLM). Role = `diff_reviewer`. Reads `previous_sub_reports`, `sub_reports`, `diff_against`, `current_run_id` from `ctx.inputs`. Calls `DiffTool.safe_run(...)`. Emits `diff_payload`. |
| `tests/test_diff_tool.py` | 10 tests: empty when no changes, added, removed, severity changed, score changed, id-first identity, fallback identity, multi-page namespacing, missing payload, summary count. |
| `tests/test_diff_reviewer.py` | 4 tests: calls tool with inputs, emits diff payload, handles missing previous, handles tool failure (returns empty `diff_payload` with summary `diff unavailable: <reason>`). |

### M10 modifications

- `src/dhrubo/core/slug.py` — added `slugify(value, *, max_len=48)`
  helper (lowercase, hyphenate, trim). Used by the issue-id
  builder.
- `src/dhrubo/agents/llm_agent.py` — `LLMAgent._to_result` now
  back-fills `id` on every issue via `populate_issue_ids(...)`
  after the LLM response is parsed.
- `src/dhrubo/agents/seo_reviewer.py`, `ui_reviewer.py`,
  `performance_reviewer.py`, `accessibility_reviewer.py`,
  `security_reviewer.py`, `branding_reviewer.py` — added
  `id: str | None = None` to every `*Issue` Pydantic model. Pure
  additive change; no schema break.
- `src/dhrubo/agents/report_writer.py` — added
  `_render_diff_section(lines, diff_payload, run_id)`. When
  `ctx.inputs["diff_against"]` is set, prepend the H2 section
  before the existing snapshot/summary content. Per-page changes
  are grouped under `### Page N — <title>` sub-headings.
- `src/dhrubo/agents/exporter.py` — extended `execute()` to:
  1. Read `ctx.inputs["sub_reports"]` and write it into
     `data.json["sub_reports"]` before serialising.
  2. Append a row to `runs/<ts>_<host>/index.json` (create-or-
     append).
  3. Resolve `diff_payload` from `ctx.inputs` and write
     `diff.json` next to `data.json` when set.
  - `n_pages` calc: `len(pages)` when populated, else
    `1 if not sub_reports else len(sub_reports)` — single-page
    runs with an empty `sub_reports` payload still record `1`.
  - New helpers: `_write_run_index`, `load_run_index`,
    `load_sub_reports_for_run` — exported and used by the CLI.
- `src/dhrubo/workflows/website_audit_pipeline.py` — `build_website_
  audit_workflow(urls, diff_against=None)` now inserts a `diff`
  task between `report` and `export` when `diff_against` is set.
  The `report` task's `output_keys` grows to
  `("final_report_md", "sub_reports")` so the diff task has
  current-run sub-reports in `ctx.inputs["sub_reports"]`. The
  `export` task's `input_keys` grows to include `diff_payload`
  and `diff_against` when set.
- `src/dhrubo/commands/cli.py` — added `--diff-against TEXT` flag.
  Resolution walks every `runs/<ts>_<host>/index.json`, looks up
  the row, and loads the `sub_reports` dict from the previous
  run's `data.json`. The dict is injected via
  `engine.run(initial_inputs={"previous_sub_reports": ...,
  "diff_against": run_id})`. Resolution happens **after** the
  `--plan-only` short-circuit so plan-only with a bogus run_id
  still succeeds.
- `src/dhrubo/agents/__init__.py` — registers `DiffReviewerAgent`.
- `config/permissions.yaml` — adds `diff_reviewer` (tools:
  `[diff]`).
- `config/retry_policies.yaml` — adds `diff_compute` (1 attempt,
  no retry — pure local diff).
- `tests/test_report_writer.py` — 3 new tests:
  `test_single_page_with_diff_renders_diff_section`,
  `test_multi_page_with_diff_renders_per_page_changes`,
  `test_no_diff_section_when_diff_against_unset`.
- `tests/test_workflows.py` — 5 new tests:
  `test_diff_task_in_dag_when_diff_against_set`,
  `test_no_diff_task_when_diff_against_unset`,
  `test_diff_task_depends_on_report`,
  `test_export_task_reads_diff_payload`,
  `test_report_emits_sub_reports_output_key`.
- `tests/test_exporter.py` — 4 new tests:
  `test_exporter_writes_run_index`,
  `test_exporter_writes_sub_reports_into_data_json`,
  `test_exporter_writes_diff_json_when_diff_payload_set`,
  `test_load_sub_reports_for_run_resolves_relative_path` (regression
  for the cross-platform path-resolution fix).
- `tests/test_cli.py` — 2 new tests:
  `test_cli_accepts_diff_against_flag`,
  `test_cli_resolves_diff_against_to_sub_reports`.

## DAG topology

Default (no diff, single- or multi-page) — unchanged from M9::

    plan → page_indexer → ... → report → export

When `--diff-against <run_id>` is set::

    plan → page_indexer → ... → report → diff → export

The `diff` task depends on `report` (needs the current run's
sub-reports in `ctx.inputs["sub_reports"]`) and the previous
run's `previous_sub_reports` (resolved by the CLI from the run
index and injected via `initial_inputs`). Its output key is
`diff_payload`; the export task reads it and writes `diff.json`
+ a `## Diff vs <run_id>` section in `report.md`.

For multi-page diffs, the diff is per-page:
`diff.added`/`removed`/`severity_changed`/`score_changed` rows
each carry a `page` key (`"0"`, `"1"`, ...) so the renderer can
group changes under the right `## Page N — <title>` H2 inside the
top-level `## Diff vs <run_id>` block.

## Reused components

- **`safe_slug()`** in `src/dhrubo/core/slug.py` — reuse for the
  issue `id` builder (`slugify()` reuses the same regex set).
- **`AgentRegistry`** — `DiffReviewerAgent` registers via
  `__init_subclass__`. Role = `diff_reviewer`.
- **`SafeRunTool` / `_do_call` test seam** — same pattern as
  every other tool in `tools/`.
- **`SessionMemory.write(key, value)`** flat namespace — the
  `diff` task reads namespaced keys (`sub_reports`,
  `previous_sub_reports`) just like the `report` task reads
  `page_<i>_seo_report` in M9.
- **`Task.metadata["inputs"]`** injection (engine.py:239) —
  used by the CLI to inject `previous_sub_reports` into the
  `diff` task. No engine change.

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
python -m ruff check .            # All checks passed!
python -m mypy src                # no issues found in 57 source files
python -m pytest -q               # 244 passed, 5 skipped

# End-to-end: baseline run
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
# → output/20260701T.../example.com/
#     report.md  data.json  pages.json (single-page: empty [])  index.json
# → data.json has a "sub_reports" key with all 6 lens payloads.
# → index.json row: {run_id, ts, target_url, target_urls,
#     seed_domain, n_pages, sub_reports_path, pages_json_path,
#     diff_against: null}

# Capture the run_id (timestamp + slug)
$RUN1 = (Get-ChildItem output -Directory | Sort-Object LastWriteTime -Desc | Select-Object -First 1).Name

# End-to-end: diff run
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf --diff-against $RUN1
# → diff task runs (logged: "diff.computed" with summary).
# → report.md now starts with "## Diff vs $RUN1" listing any
#   added/removed/severity-changed/score-changed issues per lens.
# → diff.json written alongside data.json.
# → runs/<ts2>_<host>/index.json appended with the new row, the
#   row's `diff_against` field set to $RUN1.

# Multi-page diff
python -m dhrubo.commands.cli run-audit --pages https://example.com/,https://www.iana.org/ --no-pdf
python -m dhrubo.commands.cli run-audit --pages https://example.com/,https://www.iana.org/about.html --no-pdf --diff-against $PREV_RUN_ID
# → diff section grouped by page ("### Page 1 — …", "### Page 2 — …").
```

## Sample end-to-end output

After two consecutive `run-audit --url https://example.com/ --no-pdf`
runs (the second with `--diff-against $RUN1`), the second
run's `data.json` carries::

    {
      "target_url": "https://example.com/",
      "target_urls": ["https://example.com/"],
      "seed_domain": null,
      "pages": [],
      "sub_reports": {
        "seo_report":     {"score": 80, "issues": [{"id": "mock-response:ea316940", ...}, ...]},
        "ui_report":      {...},
        "performance_report": {...},
        "a11y_report":    {...},
        "security_report": {...},
        "branding_report": {...}
      },
      "diff_against": "20260701T190308Z_example.com",
      ...
    }

`diff.json` next to it::

    {
      "run_id_a": "20260701T190308Z_example.com",
      "run_id_b": "current",
      "added": [],
      "removed": [],
      "severity_changed": [],
      "score_changed": [],
      "summary": "0 added, 0 removed, 0 severity-changed, 0 score-changed"
    }

The second run's `report.md` opens with::

    ## Diff vs 20260701T190308Z_example.com

    _0 added, 0 removed, 0 severity-changed, 0 score-changed._

    (No structural changes since the previous run.)

    ## Summary
    ...

And the new run's `index.json` row records::

    {
      "run_id": "20260701T190549Z_example.com",
      "ts": "20260701T190549Z",
      "target_url": "https://example.com/",
      "target_urls": ["https://example.com/"],
      "seed_domain": null,
      "n_pages": 1,
      "sub_reports_path": "output\\20260701T190549Z_example.com\\data.json",
      "pages_json_path": "output\\20260701T190549Z_example.com\\pages.json",
      "diff_against": "20260701T190308Z_example.com"
    }

## Risks

- **Noisy diffs from LLM issue-text drift** — mitigated by the
  new `id` field (content hash of `title|detail|severity`). If
  the LLM rewords an issue, the `id` is stable and the diff is
  silent on text-only changes.
- **Run index corruption** — single-writer (the exporter). If a
  concurrent run lands in the same `runs/<ts>_<host>/`, it's a
  different `ts`, so no contention. Worst case: the index row's
  `ts` is just slightly later than the directory name.
- **Missing prior run** — `--diff-against <unknown_id>` →
  CLI exits with `Error: run_id '<id>' not found in any
  index.json under <output>.` Defensive; never silently produces
  an empty diff.
- **Cross-platform path resolution** — the `sub_reports_path`
  stored in `index.json` is the absolute-on-Windows form
  (`output\<ts>_<host>\data.json`). The resolver tries the path
  verbatim first, then falls back to joining it with
  `output_root`. Covered by
  `test_load_sub_reports_for_run_resolves_relative_path`.
- **Sub-reports bloat `data.json`** — single-page sub-reports
  add ~10 KB; multi-page scales linearly (~10 KB × N pages).
  Acceptable for human-readable JSON; the diff tool only loads
  the `sub_reports` key, not the rendered Markdown.
- **Per-lens sub-report schema drift** — if a future milestone
  adds a field to `*Report` and a previous run's `sub_reports`
  lacks it, the diff ignores the new field (forward-compat by
  construction; we diff a hard-coded set of keys).
- **Backward compat** — single-URL / multi-page flows without
  `--diff-against` are unchanged. The `sub_reports` key in
  `data.json` is purely additive. The `id` field on issues
  defaults to `None` and is back-filled at reviewer time.
- **Concurrency** — diff is purely local compute (no I/O),
  so the diff task finishes in <100 ms. No concurrency
  concerns.

## Out of scope for M10

- **Time-range diffs** ("all runs in the last 7 days") — only
  `--diff-against <single_run_id>` is supported. Time-range
  queries are a thin layer over the run index; deferred.
- **Diff across hosts** — the diff is per-host (per
  `seed_domain`). Cross-host diffs are out of scope.
- **Visual diffs** (pixel-comparing screenshots between runs).
  Out of scope; data diff only.
- **Notification adapters** (Slack, email, GitHub PR comments).
  A future milestone could add a `dhrubo publish` subcommand
  that consumes `diff.json`.
- **Concurrent writes to the same `<ts>_<host>` directory** —
  impossible by construction (`ts` is second-resolution but the
  workflow's runtime guarantees serial export).

## Migration to M11

After M10 the audit is **history-aware**: a single run can be
diffed against a prior run via `--diff-against <run_id>`.
Candidate next directions:

- **Time-range diffs + scheduled audits** — `dhrubo run-and-diff
  --since 7d` becomes a primitive; the run index supports it.
- **CI integration** — `dhrubo publish --github-pr <n>` posts
  `diff.json` as a PR comment.
- **Tone-of-voice reviewer** — 7th lens, slot into
  `_PER_URL_TASKS`; diff framework immediately supports it.
- **Browser pooling / multi-tab** — speed up multi-page; the
  diff pipeline is per-host so the speedup benefits diff runs too.

User picks the next direction at the end of M10.