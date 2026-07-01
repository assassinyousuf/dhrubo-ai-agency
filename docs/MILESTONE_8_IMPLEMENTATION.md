# Milestone 8 — Security Reviewer + Branding Reviewer

> **Status:** complete · **Tests:** 191 passing (5 environment-skipped) · **Lint/Types:** clean

## What M8 delivered

Two new review lenses — **Security** (HTTP transport security
headers) and **Branding** (brand identity, logo, color palette,
social presence). The audit now reports on six lenses: SEO, UI,
Performance, Accessibility, Security, Branding. Both reviewers
follow the hybrid tool + LLM pattern from M5/M7: a deterministic
tool fetches/extracts signals, the LLM triages and explains them,
and either step's failure degrades gracefully.

User-confirmed design choices:

1. **Two reviewers in one milestone** — `SecurityReviewerAgent` +
   `BrandingReviewerAgent`. Two new sections in the report.
2. **Security data source** — reuse the existing `WebFetchTool`
   (httpx GET; core dep). SecurityTool wraps it: pulls the
   response, parses security-relevant headers (CSP, HSTS,
   X-Frame-Options, Referrer-Policy, Permissions-Policy,
   X-Content-Type-Options, Set-Cookie flags), checks HTTPS
   scheme and mixed-content hints.
3. **Branding data source** — DOM + meta tags only (no
   screenshot-based color clustering). The crawler's `_MetaExtractor`
   was extended to pull favicon URLs and social-link presence
   (twitter/x/linkedin/github/facebook/instagram/youtube/tiktok).
   `BrandingTool` reads `page_metadata` and re-fetches `dom_html`
   via `WebFetchTool` if needed, scanning inline `<style>` blocks
   for `--*-color` CSS vars + plain color/background-color
   declarations (regex; no new deps).
4. **Severity mapping** — both reviewers reuse the existing
   `critical/major/minor/info` scale — same rubric as the other
   reviewers. The LLM confirms/refines wording in the editor pass.
5. **Skip behavior** — graceful skip with `_NO_*_DATA_REPORT`
   (`score=None` + one `info` issue) on tool failure or missing
   data. The audit never fails. Mirrors the M5/M6/M7 skip patterns.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/tools/security_tool.py` | `SecurityParams`, `SecurityTool` calling `WebFetchTool.safe_run()` under the hood, parses security headers from the response dict. `_do_call` is the test seam. `_SECURITY_HEADERS` lists the 8 known headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP, CORP). Severity mapping: missing CSP → critical; HTTPS downgrade / mixed content → critical; missing HSTS on HTTPS → major; insecure cookies → major; missing X-Content-Type-Options → minor; missing Referrer-Policy → minor; missing Permissions-Policy → minor; server banner version leak → info. Skip payload returned when the target is unreachable. |
| `src/dhrubo/tools/branding_tool.py` | `BrandingParams`, `BrandingTool`. Pure-function helpers (`_extract_brand_colors`, `_extract_social_links`, `_extract_title_variants`, `_extract_brand`, `_grade`). Regex over inline `<style>` blocks for hex colors (`#abc` / `#abcd` / `#abcdef` / `#abcdef12`); CSS-var color declarations get normalized to the long form. Logo URL precedence: `og:image` → `twitter:image` → first favicon. Checks: `no-logo` (major), `no-theme-color` (minor), `low-social-presence` (minor), `title-inconsistent` (minor), `brand-colors-detected` (info). Skip payload when no `page_metadata` AND no `dom_html`. |
| `src/dhrubo/agents/security_reviewer.py` | `SecurityIssue`, `SecurityReport`, `_NO_SECURITY_DATA_REPORT` constant, `SecurityReviewerAgent(LLMAgent)`. Hybrid shape (tool → LLM editor pass). Side-channel `ctx.metadata["_security_payload"]` passes the tool payload from `execute()` to `build_variables()`. Back-fills `checks_count` / `headers_seen` / `headers_missing` / `scheme` / `is_https` / `server_banner` / `cookie_flags` / `final_url` / `fetched_at` / `skipped` from the tool payload. |
| `src/dhrubo/agents/branding_reviewer.py` | `BrandingIssue`, `BrandingReport`, `_NO_BRANDING_DATA_REPORT` constant, `BrandingReviewerAgent(LLMAgent)`. Hybrid shape (tool → LLM editor pass). Side-channel `ctx.metadata["_branding_payload"]`. Back-fills `checks_count` / `logo_url` / `favicons` / `og_image` / `twitter_image` / `theme_color` / `brand_colors` / `social_links` / `title_variants` / `final_url` / `fetched_at` / `skipped`. |
| `tests/test_security_tool.py` | 11 tests: skip when unreachable, skip when web fetch raises, severity mapping (CSP-missing → critical, HTTPS-downgrade → critical, insecure-cookies → major, missing HSTS → major), case-insensitive header parsing, URL validation, retry on transient error, `is_available` returns True. |
| `tests/test_branding_tool.py` | 17 tests: pure-function helpers (brand colors from inline style, short-hex normalization, empty html, social links from metadata, social links dedupes, social links from html, brand prefers og:image, brand falls back to favicon), tool runtime (extracts logo, extracts brand colors, extracts social links, flags missing logo, flags low social presence, title consistency, skip when no metadata no html, URL validation, is_available). |
| `tests/test_security_reviewer.py` | 8 tests: skip when no target URL, skip when tool returns error, happy path (LLM-driven score + back-fill from tool payload), retry on invalid JSON, retry on schema fail, missing LLM, schema sanity (score optional, rejects bad severity). |
| `tests/test_branding_reviewer.py` | 8 tests: mirror of the security reviewer tests — skip paths, happy path with back-fill of logo/og_image/theme_color/brand_colors/social_links/title_variants, retry on invalid JSON, retry on schema fail, missing LLM, schema sanity. |
| `tests/test_website_crawler.py` | 6 tests: `_MetaExtractor` pulls favicons from `<link rel="icon|shortcut icon|apple-touch-icon">`, pulls social links from `<a href>` to social hosts, empty HTML yields empty collections, `CrawledPage` carries favicons and social_links, defaults to empty collections, end-to-end the crawler surfaces the new fields on `page_metadata`. |

### M8 modifications

- `src/dhrubo/agents/website_crawler.py` — extended `_MetaExtractor`
  with `favicons` and `social_links` fields; the parser recognizes
  favicon-family `<link rel="...">` tags and filters `<a href>` to
  the social-host allowlist (`twitter.com`, `x.com`, `linkedin.com`,
  `github.com`, `facebook.com`, `instagram.com`, `youtube.com`,
  `tiktok.com`). `CrawledPage` now exposes `favicons: list[dict]`
  and `social_links: list[dict]`. `_extract()` returns the new
  fields, and `CrawledPage` construction passes them through to
  `page_metadata`.
- `src/dhrubo/agents/__init__.py` — registers
  `SecurityReviewerAgent`, `SecurityReport`, `SecurityIssue`,
  `BrandingReviewerAgent`, `BrandingReport`, `BrandingIssue`.
- `src/dhrubo/llm/mock_provider.py` — adds `_FALLBACK_SECURITY`
  constant (score=70, mock info issue, `scheme="https"`,
  `is_https=True`) and `_FALLBACK_BRANDING` (score=65, mock info
  issue, empty `brand_colors`/`social_links`/`favicons`). Keyword
  branches in `_fallback_for`:
  - Branding: `branding`, `brand`, `logo`, `favicon`,
    `theme color`, `theme-color`, `social link`, `og:image`,
    `twitter:image`, `brand colors`.
  - Security: `security`, `csp`, `hsts`, `x-frame-options`,
    `referrer-policy`, `permissions-policy`,
    `strict-transport-security`, `mixed content`,
    `secure; httponly`.
  - The `_fallback_for` helper now uses a `_kw` /
    `_kw_any` regex-word-boundary match to avoid false positives
    (e.g. `aria` no longer matches inside `variants`). Branches are
    ordered more-specific-first: SEO → UI → A11y → Branding →
    Security → Performance → Planner.
- `src/dhrubo/agents/report_writer.py` — `input_keys` extended
  with `"security_report"` and `"branding_report"`. New
  `## Security Review` section between Accessibility and Branding,
  with: score (`n/a (Security review skipped)` when skipped),
  summary, scheme, HTTPS line, headers-seen/missing count, server
  banner (if present), header-checks table (cap 10: `| Header /
  Check | Present | Severity | Finding |`), cookie-flags table
  (cap 10: `| Name | Secure | HttpOnly | SameSite |`), and
  severity-rated issues.
  New `## Branding Review` section between Security and Methodology,
  with: score, summary, logo URL, OG/Twitter image, theme color,
  brand colors list, favicons table (cap 8: `| URL | Sizes | Type |`),
  social links list (cap 8), title-variants table, and
  severity-rated issues. Methodology blurb → v0.5 ("SEO, UI,
  Performance, Accessibility, Security, Branding").
  `report_metadata["sections"]` adds `"security"` and `"branding"`;
  `["sub_reports"]` adds `"security_report"` and `"branding_report"`.
- `src/dhrubo/workflows/website_audit_pipeline.py` — adds
  `security_review` task (role `security_reviewer`,
  `depends_on=["crawl"]`, input keys `("target_url",
  "page_metadata")`, output key `("security_report",)`) and
  `branding_review` task (role `branding_reviewer`,
  `depends_on=["crawl"]`, input keys `("target_url",
  "page_metadata", "dom_html")`, output key
  `("branding_report",)`). The `report` task's `depends_on` and
  `input_keys` are extended to consume both new sub-reports.
  Docstring DAG diagram updated to the M8 topology.
- `config/permissions.yaml` — adds `security_reviewer` (tools:
  `[security]`) and `branding_reviewer` (tools: `[branding]`).
- `config/retry_policies.yaml` — adds `security_scan` (3 attempts,
  0.5s → 5s, jittered) and `branding_scan` (3 attempts, 0.5s → 5s,
  jittered).
- `tests/test_workflows.py` — adds 4 new tests:
  `test_security_review_node_in_dag`,
  `test_branding_review_node_in_dag`,
  `test_report_waits_on_security_and_branding`,
  `test_workflow_validates_after_m8`.

## DAG topology

After M8::

    planner
        │
        ▼
    website_crawler ─┬─► screenshot_agent ──┬─► ui_reviewer
                      │                      │
                      ├─► seo_reviewer ──────┤
                      │                      │
                      ├─► performance_review ┤
                      │                      │
                      ├─► accessibility_review
                      │                      │
                      ├─► security_review ───┤
                      │                      │
                      └─► branding_review ───┘
                                             ▼
                                        report_writer
                                             │
                                             ▼
                                          exporter

Both `security_review` and `branding_review` depend on `crawl`
(need `page_metadata`; branding additionally consumes `dom_html`
for inline CSS scanning).

## Verification

```powershell
cd "D:\website analyzer\dhrubo-ai-agency"
python -m ruff check .             # All checks passed!
python -m mypy src                 # no issues found in 52 source files
python -m pytest -q                # 191 passed, 5 skipped

# End-to-end smoke (no LLM keys)
python -m dhrubo.commands.cli run-audit --url https://example.com/ --no-pdf
# → report.md now contains "## Security Review" and "## Branding Review"
#   sections, with mock-fallback copy. Methodology blurb → v0.5
#   (SEO, UI, Performance, Accessibility, Security, Branding).
```

## Sample end-to-end output

After running the audit on `https://example.com/` with no API keys
configured, the report's `## Security Review` section shows:

```
**Score:** 70/100
**Summary:** Mock security audit — set OPENAI_API_KEY for real header analysis.
_Scheme:_ `https`
_HTTPS:_ yes
_Headers seen:_ 0  ·  _missing:_ 8
_Server banner:_ `cloudflare`

**Header checks:**
| Header / Check | Present | Severity | Finding |
|---|---|---|---|
| `content-security-policy` | no | minor | `content-security-policy` not present |
| `strict-transport-security` | no | minor | `strict-transport-security` not present |
...
```

And `## Branding Review`:

```
**Score:** 65/100
**Summary:** Mock branding audit — set OPENAI_API_KEY for real brand-identity analysis.
_Logo URL:_ `data:,`
_Brand colors:_ `#eeeeee`, `#334488`

**Favicons:**
| URL | Sizes | Type |
|---|---|---|
| `data:,` | — | — |

**Title variants:**
| Source | Title |
|---|---|
| page | Example Domain |
```

## Risks

- **Security header heuristics drift** — OWASP guidance evolves.
  `_grade_headers` rules live in one function; future tweaks are
  local.
- **Branding "score" is subjective** — there's no industry rubric.
  The LLM editor pass is the primary grader; the deterministic
  checks are presence-only signals (logo present? social links > 2?).
- **WebFetchTool retries inside SecurityTool** — both layers retry.
  Worst case: `security_scan.max_attempts × web_fetch retry (default
  3)` = 9 attempts on a flaky network. The `web_fetch` retry uses
  the `default` policy (1s → 30s); `security_scan` is short
  (0.5s → 5s) to keep wall time sane.
- **Inline-CSS regex is brittle** — color extraction is best-effort
  with a regex pass over `<style>` blocks. Acceptable for M8; the
  LLM editor pass does the real grading.
- **No screenshot-based color analysis** — palette is from CSS vars
  + named colors only. A future milestone could add browser-rendered
  dominant-color extraction.
- **Privacy** — security/branding fetch the page over the public
  internet via httpx, same as the existing crawler. No new PII
  surface area.

## Out of scope for M8

- Browser-driven security checks (mixed-content from a rendered
  page) — requires Playwright. Future work.
- Screenshot-based dominant-color extraction.
- Tone-of-voice analysis — LLM-only pass over body copy.
- Per-rule deep dives beyond presence checks (e.g., analyzing the
  CSP `script-src` allowlist).
- CI integration — neither axe nor security/branding have a
  GitHub Actions workstream yet.
- Multiple URLs — single page per audit, same as M7.

## Migration to M9

After M8 the audit covers **six lenses**: SEO, UI, Performance,
Accessibility, Security, Branding. M9 candidates:

- Comparison/diff runs — vector memory + change detection between
  consecutive audits.
- Multi-page audits — crawl + audit N URLs in one run.
- CI / webhook integration — GitHub Actions workflow that posts
  the report to a PR.
- Tone-of-voice reviewer — LLM-only pass over body copy.

None pre-decided; user picks the next direction at the end of M8.