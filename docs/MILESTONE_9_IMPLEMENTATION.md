# Milestone 9 — Multi-page audits

> **Status:** complete · **Tests:** 216 passing (5 environment-skipped) · **Lint/Types:** clean

## What M9 delivered

The audit now covers **N URLs in one run** while preserving the
single-URL flow as a degenerate case (no breaking change). The user
passes `--pages <a,b,c>` (comma-separated; cap 25) and the pipeline
fans out per-URL, producing one combined `report.md` with a
cross-page summary at the top and per-page sub-sections inside each
review lens.

User-confirmed design choices:

1. **Input shape.** `--pages <a,b,c>` (comma-separated) accepts a
   list of URLs. `--url` (single) is preserved as a backward-compat
   shortcut for `--pages <url>`. The two flags are mutually exclusive.
2. **Output shape.** One combined `report.md` with a cross-page
   summary at the top + per-page sub-sections (one H2 per page, six
   H3 review sections underneath). One `data.json` containing all
   per-page payloads + a `pages.json` index. No N separate report
   files; one file per audit run.
3. **No planner / dependency changes** — the engine builds the DAG
   directly via `build_website_audit_workflow(urls)`. Same
   `plan → page_indexer → crawl → reviewers → report → export`
   shape, just fanned out per URL.
4. **No new dependencies.** All multi-page plumbing is in pure
   Python + existing framework primitives.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/agents/page_indexer.py` | `Page(BaseModel) {index, url, slug}`, `PageIndex(BaseModel) {pages, seed_domain}`, `PageIndexerAgent(BaseAgent)`. Deterministic — no LLM. Reads `target_urls` (preferred) or falls back to `[target_url]`. Strips whitespace, dedupes empties, strips `www.` from the seed domain. |
| `src/dhrubo/core/slug.py` | `safe_slug(value)` — replaces unsafe chars with `_`, collapses runs, trims to 80 chars. Centralized so the indexer and exporter share one rule. |
| `tests/test_page_indexer.py` | 8 tests: single-URL passthrough, multi-URL passthrough, order preserved, `www.` stripped, whitespace stripped, fail-on-empty, Pydantic `Page` + `PageIndex` round-trip. |
| `tests/test_report_writer.py` | 4 tests: single-page M8 layout byte-stable, multi-page summary + per-page sections, missing per-page report renders as `n/a`, single-page with `pages=[…]` input keeps M8 layout. |

### M9 modifications

- `src/dhrubo/workflows/website_audit_pipeline.py` — major
  refactor of `build_website_audit_workflow(urls: list[str] | None = None)`.
  - Single-page (`urls is None or len(urls) == 1`) preserves the M8
    task IDs verbatim: `plan → page_indexer → crawl → screenshots →
    [seo, ui, perf, a11y, security, branding] → report → export`.
    Only difference from M8: a `page_indexer` task now precedes the
    crawler (uniform input shape across runs).
  - Multi-page (`len(urls) >= 2`) fans out per URL with namespaced
    `task_id`s (`crawl_0`, `crawl_1`, ..., `seo_review_0`, ...).
    Each reviewer's `output_keys` becomes
    `page_<i>_<base_key>` (e.g. `page_0_seo_report`).
    Per-URL injection uses `Task.metadata["inputs"]` —
    `metadata={"inputs": {"target_url": url}}` — no engine change.
    The `report` task depends on every per-URL task and exports a
    dynamic `input_keys` list.
  - `_screenshots_deps = {"ui_review", "a11y_review"}` (UI + a11y
    still hang off `screenshots`, others off `crawl`) — preserves
    M8 dependency shape on both paths.
  - The module docstring includes both DAG shapes.
- `src/dhrubo/agents/report_writer.py` — refactored into pure
  per-section helpers (`_render_seo`, `_render_ui`, `_render_perf`,
  `_render_a11y`, `_render_security`, `_render_branding`,
  `_render_snapshot`, `_render_screenshots`). Two top-level
  renderers:
  - `_render_single_page_top()` — M8 byte-stable layout (H2 per
    section).
  - `_render_multi_page()` — H2 per page (`## Page 1 — <title>`),
    H3 per lens (`### SEO Review`, etc.). Adds `## Summary`
    cross-page table + best/worst-per-lens table. Methodology blurb →
    v0.6 (mentions "N pages").
  - `_render_page_block(lines, meta, screenshots, sub_reports,
    *, headings=False)` — shared between paths. When
    `headings=True` (multi-page) it emits the per-page H3 headers.
  - `_collect_multi_page_payloads(ctx, n)` reads namespaced keys
    (`page_<i>_seo_report`, …) from `ctx.inputs`. Missing payloads
    become empty dicts; the per-section renderers fall back to
    "n/a (skipped)".
  - `report_metadata["pages"] = [p["url"] for p in pages]` and
    `["seed_domain"]` are added on the multi-page path.
- `src/dhrubo/commands/cli.py` — `url` made optional; added
  `--pages TEXT` (comma-separated). Mutual exclusion check, max 25
  URLs. The CLI now:
  - Prints the task count from the workflow it actually built (was
    previously hardcoded to `plan_only()`'s single-URL build).
  - `initial_inputs` includes both `target_url` and
    `target_urls = [urls...]`.
  - Calls `build_website_audit_workflow(urls=target_urls)`.
- `src/dhrubo/agents/exporter.py` — `input_keys` extended with
  `target_urls`, `seed_domain`, `pages`. Run-dir slug uses
  `seed_domain or str(target_url)` (no scheme). Multi-page runs
  also write `pages.json` (one entry per page with `index`, `url`,
  `final_url`, `title`; back-filled from `page_<i>_page_metadata`).
  `export_paths["pages_json"]` is only set when `pages` is
  non-empty.
- `src/dhrubo/agents/__init__.py` — registers `PageIndexerAgent`,
  `Page`, `PageIndex`.
- `config/permissions.yaml` — adds `page_indexer` (tools: `[]`,
  deterministic agent).
- `tests/test_cli.py` — adds 4 tests: `--pages` accepted, `--url` +
  `--pages` mutually exclusive, neither flag → error, `> 25` URLs
  rejected.
- `tests/test_exporter.py` — adds 2 tests: `seed_domain` slug,
  `pages.json` index written.
- `tests/test_workflows.py` — adds 7 M9 tests:
  `test_page_indexer_node_in_dag`,
  `test_single_page_dag_preserves_m8_task_ids`,
  `test_multi_page_dag_creates_per_url_tasks`,
  `test_per_url_tasks_inject_target_url`,
  `test_report_aggregates_all_pages`,
  `test_export_task_accepts_seed_domain_and_pages`,
  `test_workflow_validates_after_m9`.

## DAG topology

For N URLs (M9 multi-page shape)::

    plan
      │
      ▼
    page_indexer                 (single source of truth for N + URLs)
      │
      ├─► crawl_0  ─┬─► screenshots_0 ─┬─► seo_0 … branding_0 ─┐
      ├─► crawl_1  ─┤                  ├─► seo_1 … branding_1 ─┤
      │   …        │                  │   …                    │
      └─► crawl_{N-1} ─┴─► screenshots_{N-1} ─┴─► seo_{N-1} … branding_{N-1} ─┘
                                              ▼
                                            report              (aggregates all pages)
                                              │
                                              ▼
                                            export

For 1 URL (backward-compat path) — M8 + `page_indexer` prefix::

    plan → page_indexer → crawl → screenshots → [seo, ui, perf, a11y, sec, brand] → report → export

The aggregator `report` task reads `pages: list[dict]` plus the
per-page sub-reports via namespaced keys
(`page_0_seo_report`, `page_1_seo_report`, …). The
`build_website_audit_workflow` builder computes this list
dynamically; no engine changes were required.

## Reused components

- `build_website_audit_workflow(urls)` is a function, not module
  state — pure refactor.
- `Task.metadata["inputs"]` injection (engine line 239) is the
  per-URL input vector. No engine change.
- `SessionMemory.write(key, value)` stays flat — namespacing is by
  `page_<i>_<key>` (e.g. `page_0_seo_report`,
  `page_1_page_metadata`).
- `_severity_badge` + `_SEVERITY_ORDER` reused for per-page
  sections.
- `safe_slug()` (`src/dhrubo/core/slug.py`) replaces the legacy
  exporter-internal `_safe_dir_name` (kept as a thin wrapper).
- `MockProvider`/`OpenAICompatibleProvider` unchanged — the page
  indexer is deterministic.

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
python -m ruff check .            # All checks passed!
python -m mypy src                # no issues found in 54 source files
python -m pytest -q               # 216 passed, 5 skipped

# End-to-end smoke (single-URL backward compat)
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
# → "Pipeline plan OK: 12 tasks." (plan + page_indexer + 6 reviewers
#   + crawl + screenshots + report + export).
# → report.md is M8 byte-stable: ## Page Snapshot, ## SEO Review,
#   …, ## Branding Review, ## Methodology v0.5.
# → output/20260702T.../example.com/report.md

# End-to-end smoke (multi-page)
python -m dhrubo.commands.cli run-audit --pages https://example.com/,https://www.iana.org/ --no-pdf
# → "Pipeline plan OK: 20 tasks." (1 plan + 1 page_indexer + 2×8
#   per-URL tasks + report + export).
# → report.md is M9 multi-page:
#     # Website Audit Report — <seed_domain>
#     ## Summary
#     ## Page 1 — <title>
#         ### Page Snapshot
#         ### Screenshots
#         ### SEO Review
#         ### UI Review
#         ### Performance Review
#         ### Accessibility Review
#         ### Security Review
#         ### Branding Review
#     ## Page 2 — <title>  (…same shape…)
#     ## Methodology v0.6 (mentions "2 pages")
# → output/20260702T.../example.com/
#     report.md  data.json  pages.json
# → pages.json: [{index,url,final_url,title}, …]
```

## Sample end-to-end output

After running `run-audit --pages https://example.com/,https://www.iana.org/`,
the multi-page report opens with::

    # Website Audit Report — example.com

    _Pages audited:_ 2  
    _Generated:_ 2026-07-01T18:23:54+00:00  

    ## Summary

    **Pages audited:** 2

    | # | URL | Title |
    |---|---|---|
    | 1 | `https://example.com/` | https://example.com/ |
    | 2 | `https://www.iana.org/` | https://www.iana.org/ |

    **Lens scores per page:**

    | Page | SEO | UI | Performance | Accessibility | Security | Branding |
    |---|---|---|---|---|---|---|
    | 1 | — | — | — | — | — | — |
    | 2 | — | — | — | — | — | — |

    ## Page 1 — https://example.com/

    ### Page Snapshot
    …

    ### SEO Review
    **Score:** n/a  …

And the `pages.json` index::

    [
      { "index": 0, "url": "https://example.com/",   "final_url": "https://example.com/",   "title": "" },
      { "index": 1, "url": "https://www.iana.org/", "final_url": "https://www.iana.org/", "title": "" }
    ]

## Risks

- **Per-URL failures cascade.** If `crawl_<i>` fails, its downstream
  `seo_<i>`, `ui_<i>`, … never run. The report writer handles
  missing `page_<i>_*` keys gracefully (renders as `n/a`); the
  engine already supports `result.status = PARTIAL` on per-task
  failures.
- **Concurrency.** The engine's `max_concurrency=4` default may
  queue per-URL tasks. The CLI help text suggests `--concurrency
  8-12` for multi-page.
- **Report length.** Multi-page reports grow linearly. The 25-URL
  cap keeps reports readable.
- **Per-page slug collisions** don't apply: the exporter writes one
  combined `report.md`, no per-page sub-outputs.
- **Backward compat.** Locked by
  `test_single_page_dag_preserves_m8_task_ids` and
  `test_single_page_with_pages_input_uses_single_layout`. The M8
  report layout is byte-stable on the single-page path
  (methodology blurb → v0.5).

## Out of scope for M9

- **Auto-discovery of same-domain pages** from a single seed URL
  (sitemaps, /about /pricing heuristics). Trivial extension of
  `PageIndexerAgent` later.
- **Per-page PDF rendering.** One combined `report.md` only.
- **Concurrent browser sessions.** The browser subsystem is
  single-page today; per-URL tasks share whatever driver the
  screenshot tool picks up. A future milestone could pool browsers.
- **Cross-page diff / comparison.** M9 is multi-page; M10
  candidates include diff runs between consecutive audits.

## Migration to M10

After M9 the audit covers six lenses on N pages in one run. M10
candidates:

- **Comparison/diff runs** — run a multi-page audit on Mon, run it
  again on Wed, surface what changed.
- **Tone-of-voice reviewer** — 7th lens, LLM-only pass over body
  copy.
- **CI / webhook integration** — GitHub Actions workflow.
- **Browser pooling / multi-tab** — speed up multi-page by sharing
  one Chromium instance.

User picks the next direction at the end of M9.
