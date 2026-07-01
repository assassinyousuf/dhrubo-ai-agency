# Milestone 11 — Time-range diffs + scheduled audits

> **Status:** complete · **Tests:** 282 passing (5 environment-skipped) · **Lint/Types:** clean

## What M11 delivered

The audit is now **scheduled-audit-aware**. After M10 every run
appended a row to `runs/<ts>_<host>/index.json`, and
`--diff-against <run_id>` produced a structured diff. But M10
only answered "what changed between *these two* runs". M11 adds
a thin layer over M10's diff machinery:

1. **`run-audit --diff-since <window>` flag.** When set, the
   CLI resolves the time window to the earliest run in it and
   auto-diffs against it — so a cron-driven `run-audit` always
   shows what changed since the last run without the caller
   having to remember a specific `run_id`.
2. **Standalone `dhrubo diff` subcommand.** Pure-history query:
   pick earliest + latest runs in a time window, emit a diff.
   No audit is run; no agents spin up. Reads the existing run
   index, calls the existing `DiffTool` (M10), prints a human
   summary by default or writes `diff.json` with `--json`.
3. **`--diff-since` / `dhrubo diff --since` accept both
   relative (`7d`, `24h`, `1w`) and absolute (`YYYY-MM-DD`,
   `YYYY-MM-DDTHH:MM:SSZ`) time formats.** One parser, three
   syntaxes.
4. **M10 latent bug fix.** The `## Diff vs <run_id>` section
   was supposed to be prepended to `report.md` by the report
   writer, but in the M10 DAG shape (`report → diff → export`)
   the diff wasn't computed when the report ran, so the section
   never appeared in live pipeline runs. M11 moves the
   rendering to the **exporter** (the only task with both
   `final_report_md` and `diff_payload` in its inputs at the
   same time).

User-confirmed design choices:

1. **Time format.** Relative (`7d`, `24h`, `1w`) AND absolute
   (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SSZ`). One parser, three
   syntaxes. Pairs with `--until`; defaults to "now".
2. **Scheduled-audit flag.** Add `--diff-since` to `run-audit`.
   Mutually exclusive with `--diff-against`.
3. **Standalone subcommand.** Add `@app.command("diff")` to the
   CLI, sibling of `run-audit` and `plan`.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/core/timeparse.py` | `parse_since(value)` + `parse_window(since, until)` + `Window` NamedTuple. Supports relative (`<int><m|h|d|w>`) and absolute (ISO 8601 date / datetime) values. Raises `ValueError` with friendly messages on bad input. |
| `src/dhrubo/core/run_window.py` | `select_runs_in_window(window, *, target_url, output_root) -> list[dict]`. Reads the run index, filters by `ts ∈ [start, end)` and an optional URL/host filter, returns rows sorted ascending by `ts`. |
| `src/dhrubo/core/run_index.py` | **Refactor** of `load_run_index` + `load_sub_reports_for_run` out of `agents/exporter.py` into a leaf module in `core/`. Lets `core/run_window.py` build on the index loader without dragging in the `agents.exporter` import chain. `exporter.py` re-exports the symbols for backward compat with M10 callers/tests. |
| `src/dhrubo/tools/diff_tool.py` | Added `compute_diff(...)` public function — thin wrapper over the private `_diff` that takes a dict in/out (no `ToolContext` / `safe_run` plumbing). Lets the standalone `dhrubo diff` subcommand skip the agent plumbing. |
| `tests/test_timeparse.py` | 22 tests: relative minutes/hours/days/weeks, case-insensitive, whitespace tolerance, zero/negative rejection, absolute date / date+time / date+time+Z / date+time+offset, parse_window defaults + error path. |
| `tests/test_run_window.py` | 7 tests: empty index, time filter, sort ascending, URL filter, seed_domain filter, half-open window exclusion, malformed-row skip. |

### M11 modifications

- `src/dhrubo/commands/cli.py`:
  1. `run-audit` gained `--diff-since TEXT` and `--diff-until
     TEXT` options. Mutually exclusive with `--diff-against`.
     When set, calls `parse_window(...)` → `select_runs_in_window(...)`
     → picks the earliest run as the comparison pair → funnels
     into the M10 path (`previous_sub_reports` + `diff_against`
     injection). Resolution happens **after** the plan-only
     short-circuit so `--plan-only` + `--diff-since` succeeds.
  2. New `@app.command("diff")` subcommand:
     - `--url`, `--since`, `--until`, `--output-dir`, `--json`,
       `--config` options.
     - Resolves the window via `parse_window`,
       `select_runs_in_window`, picks earliest + latest, calls
       `compute_diff(...)`.
     - Default: prints a human-friendly per-lens breakdown on
       stdout (window span, summary line, per-lens +N/-M/Δscore
       table).
     - With `--json`: writes `diff_<ts>_<host>.json` under the
       output directory.
     - Errors cleanly on empty window, missing `--url`, or
       bad time formats.
  3. New `_print_diff_summary` and `_human_duration` helpers.
- `src/dhrubo/agents/exporter.py` — **M10 bug fix**: now
  pre-pends the `## Diff vs <run_id>` H2 section to
  `report.md` (and to `data.json["report_markdown"]`) when
  `diff_payload` + `diff_against` are in its inputs. Calls the
  now-public `report_writer.render_diff_section(...)` with
  `multi_page=(len(pages) >= 2)`.
- `src/dhrubo/agents/report_writer.py`:
  1. Renamed `_render_diff_section` → `render_diff_section`
     (now public; the exporter imports it).
  2. Removed the unreachable diff-rendering block from the
     writer's `execute()` — the DAG shape means the diff
     hasn't been computed yet at report time. The exporter
     now owns the rendering.
- `src/dhrubo/agents/__init__.py` — no change.
- `src/dhrubo/core/__init__.py` — exports `Window`,
  `parse_since`, `parse_window`, `select_runs_in_window`.
- `tests/test_report_writer.py` — M10 diff-rendering tests
  moved to `tests/test_exporter.py` (the writer no longer
  renders diffs). Kept `test_no_diff_section_when_diff_against_unset`.
  Added a regression test confirming the writer ignores
  `diff_payload`.
- `tests/test_exporter.py` — 3 new tests:
  `test_exporter_prepends_diff_section_to_report`,
  `test_exporter_no_diff_section_when_unset`,
  `test_exporter_multi_page_diff_groups_by_page`.
- `tests/test_cli.py` — 7 new tests:
  - `test_cli_diff_since_flag_accepted_plan_only`
  - `test_cli_diff_since_and_diff_against_mutually_exclusive`
  - `test_cli_diff_since_bad_format_errors`
  - `test_cli_diff_since_unknown_window_errors`
  - `test_cli_diff_subcommand_requires_url`
  - `test_cli_diff_subcommand_no_runs_in_window_errors`
  - `test_cli_diff_subcommand_with_json`

## CLI surface

### `run-audit` (modified)

```text
--diff-since TEXT      Time window start for scheduled diff.
                       Pair with --diff-until. Accepts relative
                       (7d, 24h, 1w) or absolute (YYYY-MM-DD
                       or YYYY-MM-DDTHH:MM:SSZ). Mutually
                       exclusive with --diff-against.
--diff-until TEXT      Time window end (default: now).
```

When set: the CLI resolves the window to the earliest run in
it and feeds `previous_sub_reports` + `diff_against` into
`initial_inputs`. The DAG shape stays
`report → diff → export` (same as `--diff-against`).

### `dhrubo diff` (new)

```text
Usage: dhrubo diff [OPTIONS]

  Compute a diff between the earliest and latest run in a time
  window. Pure-history query against runs/<host>/index.json —
  no audit is run, no agents spin up.

Options:
  --url TEXT            Filter by target_url or seed_domain. If
                        omitted, returns one diff per host.
  --since TEXT          Window start. Same format as
                        run-audit --diff-since.
  --until TEXT          Window end. Defaults to now.
  -o, --output-dir PATH Output directory (defaults to ./output).
                        Only used with --json.
  --json                Write diff.json under <output-dir>.
                        Default prints a human summary.
  -c, --config PATH     Config directory (default ./config).
  --help                Show this message and exit.
```

Example sessions:

```text
$ dhrubo diff --url https://example.com/ --since 7d
Diff 20260702T..._example.com -> 20260708T..._example.com
  Window: 7d  (3 added, 1 removed, 0 severity-changed, 1 score-changed)
  Host: https://example.com/  Runs compared: 20260702T... .. 20260708T...
  Summary: 3 added, 1 removed, 0 severity-changed, 1 score-changed
  Per-lens breakdown:
    SEO Review             +2 -1  Δscore 0
    Security Review         +1 -0  Δscore -5

$ dhrubo diff --url https://example.com/ --since 7d --json
Wrote output\diff_20260630T..._example.com.json
```

## DAG topology

Unchanged from M10. Both new CLI surfaces funnel into the
existing `report → diff → export` DAG via `previous_sub_reports`
+ `diff_against` injection — no engine or builder edits. The
exporter's new diff-section prepending is a localized change.

## Reused components

- **`DiffTool._diff(...)`** (`src/dhrubo/tools/diff_tool.py:127`)
  — called by `DiffReviewerAgent` (M10) and now also by
  `compute_diff(...)` (M11).
- **`DiffReviewerAgent`** (`src/dhrubo/agents/diff_reviewer.py:27`)
  — unchanged; M11 reuses it inside `run-audit --diff-since`.
- **`load_run_index`** + **`load_sub_reports_for_run`** —
  refactored into `core/run_index.py` (was in `agents/exporter.py`).
  `exporter.py` re-exports them for backward compat.
- **`cli.py` flat `@app.command(...)`** structure — precedent
  is `plan` as a sibling of `run-audit`. `diff` follows verbatim.
- **`render_diff_section`** — promoted from `_render_diff_section`
  in `report_writer.py`; the exporter now calls it.
- **`CliRunner` module-level + `TemporaryDirectory()`** pattern
  in `tests/test_cli.py` — new CLI tests follow verbatim.

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
python -m ruff check .            # All checks passed!
python -m mypy src                # no issues found in 60 source files
python -m pytest -q               # 282 passed, 5 skipped

# Seed: two baseline runs for one host.
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
$RUN_EARLIEST = (Get-ChildItem output -Directory | Sort-Object LastWriteTime | Select-Object -First 1).Name
$RUN_LATEST   = (Get-ChildItem output -Directory | Sort-Object LastWriteTime -Desc  | Select-Object -First 1).Name

# Standalone diff (human summary)
python -m dhrubo.commands.cli diff --url https://example.com/ --since 1d
# → "Window: 1d  (0 added, 0 removed, 0 severity-changed, 0 score-changed)"
# → "Host: https://example.com/  Runs compared: ... .. ..."
# → "Summary: 0 added, 0 removed, ..."

# Standalone diff (json)
python -m dhrubo.commands.cli diff --url https://example.com/ --since 1d --json
# → "Wrote output\diff_..._example.com.json"

# run-audit --diff-since
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf --diff-since 1d
# → "Diff: --diff-since resolved to '<earliest>'."
# → report.md starts with "## Diff vs `<earliest>`"   ← M10 bug fixed in M11

# Mutual exclusion
python -m dhrubo.commands.cli run-audit --url https://example.com/ --diff-against $RUN_EARLIEST --diff-since 1d
# → exits with "Error: --diff-against and --diff-since are mutually exclusive."

# Empty window
python -m dhrubo.commands.cli diff --url https://example.com/ --since 1h
# → exits with "Error: no runs found in the window [...]"

# Bad time format
python -m dhrubo.commands.cli diff --url https://example.com/ --since "banana"
# → exits with "Error: could not parse --since/--until: could not parse 'banana' ..."
```

## Sample end-to-end output

After three `run-audit --url https://example.com/ --no-pdf` runs
(the third with `--diff-since 1d`), the third run's `report.md`
opens with::

    ## Diff vs `20260701T190308Z_example.com`

    _0 added, 0 removed, 0 severity-changed, 0 score-changed._

    # Website Audit Report — Example Domain
    ...

And the new run's `index.json` row records::

    {
      "run_id": "20260701T194801Z_example.com",
      "ts": "20260701T194801Z",
      "target_url": "https://example.com/",
      "target_urls": ["https://example.com/"],
      "seed_domain": null,
      "n_pages": 1,
      "sub_reports_path": "output\\20260701T194801Z_example.com\\data.json",
      "pages_json_path": "output\\20260701T194801Z_example.com\\pages.json",
      "diff_against": "20260701T190308Z_example.com"
    }

## Risks

- **Window with one run** — `dhrubo diff` warns ("only one run in
  the window; emitting an empty diff") and returns an empty
  diff. `run-audit --diff-since` warns the same way and runs
  without a diff section.
- **Clock skew** — `ts` is the exporter's UTC time at write.
  Two audits minutes apart have a stable ordering by `ts`
  (string-sorted = time-sorted). No skew risk.
- **Per-host identity** — `--url` matches `target_url` OR
  `seed_domain`. Mismatches return `0 rows` and error cleanly.
- **Cron + cold-start** — first ever run has no index → no
  `--diff-since` candidate. Handled by the empty-window error.
- **Index bloat** — append-only JSON, ~200 bytes/row. Even at
  1000 runs the file is ~200 KB and parse is fast. Acceptable;
  M12 candidate is retention policy.
- **M10 diff-section regression** — the M10 tests asserted the
  report writer renders the diff, but in the M10 DAG the diff
  isn't computed at report time. M11 moves the rendering to
  the exporter; the corresponding M10 tests moved with it
  (now in `test_exporter.py`). No loss of coverage.
- **Backward compat** — `run-audit` is purely additive (two
  new optional flags). `dhrubo diff` is a brand-new subcommand.
  The exporter's diff-section rendering is purely additive
  (only kicks in when `diff_payload` is in inputs).
- **Multi-page diff** — M10 already supports per-page diffs;
  M11 inherits. The `render_diff_section(multi_page=True)`
  branch groups rows by `page` key.
- **Unicode on Windows consoles** — `→` was replaced with `->`
  in the human summary to avoid `cp1252` encode errors. The
  JSON output keeps Unicode intact.

## Out of scope for M11

- **Rolling diffs** (compare every consecutive pair in the
  window). Just earliest ↔ latest. Rolling is a thin layer over
  the same primitive; deferred.
- **Multi-host diffs in one call** (diff across hosts). Today
  the standalone `dhrubo diff` without `--url` would emit
  one diff per host — but the CLI errors on that today. The
  per-host restriction matches M10.
- **HTML / PDF report rendering for the standalone `diff`.**
  The standalone command is a JSON / stdout summary only.
  Markdown rendering is what `run-audit --diff-since` already
  gives you via the report writer + exporter.
- **Notifications** (post the diff to Slack / GitHub PR).
  Deferred to a future milestone.

## Migration to M12

After M11 the audit is **scheduled-audit-aware**:
`run-audit --diff-since 7d` auto-diffs against the earliest run
in the window, and `dhrubo diff --since 7d` answers ad-hoc
history queries without re-running the audit. Candidate M12
directions:

- **CI integration** — `dhrubo publish --github-pr <n>` posts
  `diff.json` as a PR comment. The new `dhrubo diff --json`
  output is already the input shape.
- **Tone-of-voice reviewer** (7th lens). Slot into
  `_PER_URL_TASKS`; the diff framework (now with time-range
  queries) immediately supports it.
- **Browser pooling / multi-tab** for multi-page speedup. Pure
  perf work; benefits scheduled multi-page audits the most.
- **Run retention** (`Settings.output.retain_runs = 10`
  already exists but isn't enforced). Wire it up: prune oldest
  rows from `index.json` and their run-dirs on each export.

User picks the next direction at the end of M11.