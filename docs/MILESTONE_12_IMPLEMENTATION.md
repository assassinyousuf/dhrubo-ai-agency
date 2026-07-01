# Milestone 12 — CI integration: GitHub PR comments

> **Status:** complete · **Tests:** 338 passing (5 environment-skipped) · **Lint/Types:** clean

## What M12 delivered

The audit is now **CI-integrated**. After M10/M11 every run
writes a `diff.json`, and `run-audit --diff-since 7d` (or
the standalone `dhrubo diff --json`) produces a structured
diff on disk. M12 turns that diff into **actionable PR
feedback** automatically — humans no longer have to go look at
the file.

User-confirmed design choices:

1. **GitHub only.** M12 ships a single `--github-pr <n>` flag.
   GitLab/Bitbucket are out of scope (deferred to M13+).
2. **Read `diff.json` from disk.** No audit re-runs; the
   command is purely a publisher. The CI workflow is
   `run-audit --diff-since 7d` → `dhrubo publish --github-pr N`.
3. **Markdown + collapsible `<details>`.** Header + one-line
   summary + per-lens `+N / -M / Δscore` table + `<details>`
   blocks per lens for added/removed issues (capped at 50 per
   lens to bound comment length).

The production-shaped CI flow:

```text
nightly cron → run-audit --diff-since 7d → dhrubo publish --github-pr $PR_NUMBER
                                                       │
                                                       └──► "## Website Audit Diff" PR comment
```

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/github_comment_tool.py` | `GitHubCommentTool` with `post_pr_comment(repo, pr_number, body, *, token)`. Thin wrapper around `httpx.AsyncClient.post(...)` to `https://api.github.com/repos/<owner>/<repo>/issues/<n>/comments`. Wraps the call in `retry_async(...)` with the new `github_post` policy (3 attempts, exponential backoff, retries 5xx + network errors only; 4xx surfaces immediately). Returns the comment's HTML URL + id so the CLI can print it. |
| `src/dhrubo/tools/markdown_diff_renderer.py` | `render_diff_comment(diff_payload, *, max_issues_per_lens=50) -> str`. Pure-function Markdown renderer. Produces the comment body: H2 header, run-id sub-line, italicized summary, per-lens table, per-lens `<details>` blocks (sorted by severity, capped at 50 with `…and N more` truncation marker). No I/O. |
| `src/dhrubo/agents/publisher.py` | `PublisherAgent` (deterministic — no LLM). Role = `publisher`. Reads `diff_payload` from `ctx.inputs["diff_payload"]` (preferred) or loads from `ctx.inputs["diff_path"]`. Renders via `markdown_diff_renderer`, posts via `GitHubCommentTool.safe_run(...)`, emits `comment_url` + `comment_id`. |
| `tests/test_markdown_diff_renderer.py` | 22 tests: empty diff, header, summary, per-lens table, added/removed/severity-changed details, truncation (cap=0 / cap=10), per-lens title parametrization, severity ordering, edge cases. |
| `tests/test_github_comment_tool.py` | 13 tests: URL build, auth header, custom base URL, success path, missing `html_url`, 4xx-no-retry, 5xx-with-retry-then-succeed, transport-error retry, transport-error after retries, bad params, `is_available()`. |
| `tests/test_publisher_agent.py` | 13 tests: emits comment URL, loads diff from path, missing diff / missing repo / missing token / missing pr / zero pr / non-int pr / tool failure / tool no-data / `max_issues_per_lens` forwarded / agent registered. |
| `tests/test_cli.py` (M12 extension) | 8 new tests: help, missing token, missing diff path, bad repo shape, zero PR number, repo from env var, end-to-end success, surfaces tool failure. |

### M12 modifications

- `src/dhrubo/config/settings.py` — new `GitHubSettings`
  (api_key_env, repository_env, api_base_url). `api_key_env`
  and `repository_env` store the **names** of the env vars
  holding the secret and default repo; the CLI reads them
  with `os.environ.get(...)`. Never put the secret in YAML.
- `src/dhrubo/agents/__init__.py` — registers `PublisherAgent`
  alongside the other 14 agents.
- `config/permissions.yaml` — adds `publisher` role
  (`tools: [github_comment]`).
- `config/retry_policies.yaml` — adds `github_post` policy
  (3 attempts, 1.0s → 15.0s exponential backoff, jitter on).
  **Also fixes a pre-existing M10 latent bug** where the
  `diff_compute` policy had `0.0` delays that violated
  `RetryConfig.initial_delay_seconds > 0` and broke config
  loading. Adjusted to `0.001` (effectively zero, satisfies
  the constraint).
- `src/dhrubo/commands/cli.py` — new
  `@app.command("publish")` subcommand, sibling of `diff`,
  `plan`, `run-audit`. Resolves `repo` from `--repo` or
  `GITHUB_REPOSITORY` env; resolves `GITHUB_TOKEN` from env
  (no flag — never leak the secret via shell history);
  reads `diff.json`; calls `PublisherAgent` directly (no DAG
  — pure CLI primitive, just like `dhrubo diff`).

## CLI surface

### `dhrubo publish` (new)

```text
Usage: dhrubo publish [OPTIONS]

  Post a diff.json (produced by `run-audit --diff-since` or
  `dhrubo diff --json`) as a Markdown comment on a GitHub PR.

Options:
  --diff-path PATH              Path to diff.json on disk. Required.
  --repo TEXT                   GitHub repo (owner/name). Falls back
                                to the GITHUB_REPOSITORY env var
                                (set automatically by GitHub Actions).
  --github-pr INTEGER           PR number to comment on. Required.
  --max-issues-per-lens INTEGER Cap on issues listed per lens
                                [default: 50]. 0 = summary only.
  -c, --config PATH             Config directory [default: ./config].
  --help                        Show this message and exit.

Environment:
  GITHUB_TOKEN            (required) GitHub PAT or installation token
                          with `repo` scope. The CLI exits with an
                          error if this is unset.
  GITHUB_REPOSITORY       (optional) Fallback for --repo. Set
                          automatically by GitHub Actions.
```

Example CI workflow:

```yaml
- name: Run audit + diff
  run: |
    python -m dhrubo.commands.cli run-audit \
      --url https://example.com/ \
      --no-pdf --diff-since 7d
- name: Publish diff
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    python -m dhrubo.commands.cli publish \
      --diff-path output/${{ env.RUN_DIR }}/diff.json \
      --github-pr ${{ github.event.pull_request.number }}
# → "Comment posted: https://github.com/<owner>/<repo>/pull/<n>#issuecomment-<id>"
```

## Reused components

- **`retry_async(op, policy, op_name, retriable=...)`**
  (`src/dhrubo/core/retry.py:35`) — wraps the httpx POST.
- **`httpx.AsyncClient`** — same per-call pattern as
  `web_fetch_tool.py:55` and `lighthouse_tool.py:136`.
- **`Tool.safe_run(...)`** + `ToolContext` — same surface as
  every other tool.
- **`compute_diff(...)`** — unused here (we read the diff from
  disk, not re-compute), but a future `--diff-since` flag on
  `publish` could reuse it.
- **Settings env-var convention** — `api_key_env: str =
  "GITHUB_TOKEN"` matches `LLMSettings.api_key_env`.
- **CLI subcommand pattern** — `@app.command("publish")`
  siblings `diff`, `plan`, `run-audit`. Non-interactive
  (no prompts); errors are surfaced via `_console.print +
  typer.Exit(code=2)`.
- **`markdown_diff_renderer` ordering** — `_LENS_ORDER` /
  `_LENS_TITLES` / `_SEVERITY_RANK` match the same vocabulary
  used by the report writer and the standalone `dhrubo diff`
  subcommand.

## Comment body shape

The rendered Markdown body looks like this for a non-empty
diff:

```markdown
## Website Audit Diff

Comparing `20260701T190308Z_example.com` -> `20260702T120000Z_example.com`

_3 added, 1 removed, 0 severity-changed, 1 score-changed_

| Lens | Added | Removed | Score Δ |
|---|---:|---:|---:|
| SEO | 2 | 1 | -5 |
| UI | 0 | 0 | — |
| Performance | 0 | 0 | — |
| Accessibility | 0 | 0 | — |
| Security | 1 | 0 | 0 |
| Branding | 0 | 0 | — |

<details><summary>SEO (3 changes)</summary>

**Added (2)**

- `major` **Missing meta description** (`missing-meta-description:abc12345`)
- `minor` **Image missing alt** (`image-alt:deadbeef`)

**Removed (1)**

- `info` **Title length OK** (`title-length:11111111`)

</details>

<details><summary>Security (1 change)</summary>

**Added (1)**

- `critical` **Missing Content-Security-Policy** (`missing-csp:abcdef00`)

</details>
```

For an empty diff:

```markdown
## Website Audit Diff

Comparing `20260701T190308Z_example.com` -> `20260702T120000Z_example.com`

_0 added, 0 removed, 0 severity-changed, 0 score-changed_

_No structural changes._
```

## DAG topology

Unchanged from M11. `publish` is a CLI primitive — it does
**not** go through the workflow engine. The `run-audit
--diff-since` path that *does* use the DAG is untouched.

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
python -m ruff check .            # All checks passed!
python -m mypy src                # no issues found in 63 source files
python -m pytest -q               # 338 passed, 5 skipped

# Seed: two baseline runs for one host (M10/M11 shape).
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
$RUN_LATEST = (Get-ChildItem output -Directory | Sort-Object LastWriteTime -Desc | Select-Object -First 1).Name

# Help / shape
python -m dhrubo.commands.cli publish --help
# → shows --diff-path, --repo, --github-pr, --max-issues-per-lens, -c/--config

# Standalone diff (re-using M11 machinery) → produces diff.json
python -m dhrubo.commands.cli diff --url https://example.com/ --since 1d --json

# Missing token → clean error
python -m dhrubo.commands.cli publish `
  --diff-path output/$RUN_LATEST/diff.json `
  --repo foo/bar --github-pr 1
# → "Error: GITHUB_TOKEN env var is not set. ..."

# Missing diff path → clean error
$env:GITHUB_TOKEN = "ghp_..."
python -m dhrubo.commands.cli publish `
  --diff-path output/nonexistent.json `
  --repo foo/bar --github-pr 1
# → "Error: --diff-path does not exist or is not a file: ..."

# Bad repo shape → clean error
python -m dhrubo.commands.cli publish `
  --diff-path output/$RUN_LATEST/diff.json `
  --repo foo --github-pr 1
# → "Error: --repo must be of the form 'owner/name', got 'foo'."

# End-to-end (requires a real PR + token)
python -m dhrubo.commands.cli publish `
  --diff-path output/$RUN_LATEST/diff.json `
  --repo octocat/Hello-World --github-pr 42
# → "Comment posted: https://github.com/octocat/Hello-World/pull/42#issuecomment-<id>"
```

## Risks

- **Comment length caps.** GitHub's max comment is 65,536
  chars; the renderer caps per-lens issue lists at 50
  (configurable via `--max-issues-per-lens`). 50 issues × 6
  lenses × ~300 chars/issue ≈ 90 KB raw — under cap after
  table formatting. The cap is a guardrail; not perfect.
- **4xx vs 5xx retry policy.** 4xx (auth, not-found,
  validation) must NOT retry — same input fails the same
  way. The tool raises 5xx as `httpx.HTTPStatusError` (caught
  by `retry_async`'s `retriable=(httpx.HTTPError,)`) and
  classifies it as a `ToolError` only after retries are
  exhausted. 4xx responses short-circuit immediately.
- **Token leakage.** The token is read from env, never
  logged, never written to disk. The CLI rejects `--token`
  as a flag (env-only on purpose) so tokens don't end up in
  shell history.
- **Rate limits.** GitHub allows 5,000 req/hr for a PAT with
  `repo` scope. A single comment is 1 request. No risk for
  the audit use case.
- **Cross-platform httpx.** Already battle-tested in
  `web_fetch_tool.py` and `lighthouse_tool.py`.
- **Backward compat.** Pure addition — new subcommand, new
  config keys (all with defaults), new role + tool
  registration. No existing flag changes; no tests removed.
- **CI without a PR.** `dhrubo publish --github-pr 0` errors
  cleanly ("--github-pr must be a positive integer").
- **`diff_compute` retry policy fix.** The M10 YAML had
  `0.0` delays which violated `RetryConfig.gt=0.0` and
  broke `load_retry_policies(...)` for any caller. M12
  bumped them to `0.001` (effectively zero, satisfies the
  constraint). No semantic change — the policy is
  single-attempt and unused in code today.
- **Test mocking seam.** `GitHubCommentTool._do_call` is
  monkey-patched in tests (matching the pattern in
  `lighthouse_tool.py`). Production code calls
  `httpx.AsyncClient.post` directly.

## Out of scope for M12

- **GitLab MR comments** (different API, different auth).
- **Bitbucket PR comments.**
- **Updating an existing comment** (find-then-edit instead of
  always-post). Today each `publish` call creates a new
  comment. A future milestone could mark old comments as
  outdated.
- **Comment templating.** The Markdown layout is fixed. Future
  work could allow a `--template` flag.
- **Slack / Teams notifications.**
- **Status checks** (GitHub commit status API — "audit
  passed/failed"). Different surface.
- **Inline PR review comments** (file/line-specific). Out of
  scope; the audit output isn't file/line-aware.

## Migration to M13

After M12 the audit is **CI-integrated**: a cron'd
`run-audit --diff-since 7d` followed by `dhrubo publish
--github-pr N` produces actionable PR feedback automatically.
Candidate M13 directions:

- **Tone-of-voice reviewer** (7th lens). Slot into
  `_PER_URL_TASKS`; the diff + publish pipeline immediately
  supports it.
- **Browser pooling / multi-tab** for multi-page speedup.
- **Run retention** (wire up `retain_runs = 10`).
- **GitLab MR support** (parallel to the M12 GitHub path).
- **Find-then-update comment** (avoid comment spam).

User picks the next direction at the end of M12.
