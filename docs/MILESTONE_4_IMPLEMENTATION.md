# Milestone 4 — UI Reviewer Agent (Vision)

> **Status:** complete · **Tests:** 81 passing (1 skipped — live LLM) · **Lint/Types:** clean

## What M4 delivered

A vision-capable UI reviewer that consumes the three viewport screenshots
already produced by M3 and emits a structured UI/UX sub-report. The audit
report now covers both the **structural** (SEO) and the **visual** (UI)
side of a page. The LLM message contract was extended to support inline
images, and the OpenAI-compatible provider was taught how to emit them.

### New components

| Module | Role |
|---|---|
| `src/dhrubo/llm/interface.py` | New `ImageRef` model + `images: list[ImageRef]` on `LLMMessage`. Backwards compatible (`images` defaults to `[]`). |
| `src/dhrubo/tools/image_utils.py` | Stdlib-only helpers: `detect_media_type`, `read_bytes`, `to_data_url`. |
| `src/dhrubo/agents/ui_reviewer.py` | `UiIssue`, `UiReport`, `UiReviewerAgent(LLMAgent)`. Inherits prompt rendering, JSON-mode request, retry loop. Overrides `_call_llm` to attach `ImageRef`s. |
| `tests/test_image_utils.py`, `tests/test_ui_reviewer.py` | New test coverage. |

### M4 modifications

- `src/dhrubo/llm/openai_provider.py` — `_to_openai_msg` now emits
  `[{"type":"text",...}, *image_url_parts]` when the message carries
  images. Backward compatible: text-only messages keep the string form.
- `src/dhrubo/llm/mock_provider.py` — adds a `_FALLBACK_UI` shape +
  `ui`/`layout`/`screenshot`/`visual` keyword branch in `_fallback_for`.
- `src/dhrubo/agents/__init__.py` — registers `UiReviewerAgent` (and the
  `UiIssue` / `UiReport` types).
- `src/dhrubo/agents/report_writer.py` — adds a `## UI Review` section
  between SEO and Methodology, renders score / summary / viewports /
  issues; updates `report_metadata` and methodology blurb.
- `src/dhrubo/workflows/website_audit_pipeline.py` — adds a `ui_review`
  task parallel to `seo_review`, depending on `screenshots`; `report`
  task waits on all three sub-reports.
- `config/models.yaml` — bumps `ui_reviewer.max_tokens: 2048 → 4096`.
- `config/permissions.yaml` — `ui_reviewer.tools: []` (was
  `[vision_caption]`; vision is LLM-native, not a tool).

### New tests (26 cases)

| File | Covers |
|---|---|
| `tests/test_image_utils.py` | PNG/JPEG/WEBP detection, unknown format, data URL format, explicit media type, missing file. |
| `tests/test_ui_reviewer.py` | No-screenshot fallback, image attachment, viewports_seen back-fill, happy path, retry on invalid JSON, retry on schema fail, missing LLM, missing path dropped (not fatal), schema rejects bad severity, score optional. |
| `tests/test_llm.py` | Default `images=[]` invariant, text-only unchanged, `image_url` data URL, URL passthrough, ImageRef-without-url-or-path raises. |
| `tests/test_workflows.py` | `ui_review` node in DAG, depends on `screenshots`, `report` waits on all three sub-reports, `wf.validate()` clean. |

## Design decisions

### 1. Add `images` to `LLMMessage`, not `LLMRequest`

`LLMRequest.attachments: list[ImageRef]` was the other candidate. We chose
per-message images because:

- OpenAI's wire format attaches images to a specific user turn.
- A single request can mix text-only and image-bearing turns (e.g. tool
  results without images, user message with screenshots).
- `images: list[ImageRef] = Field(default_factory=list)` keeps every
  existing test/agent/provider call site green (invariant verified by
  `test_llm_message_default_images_is_empty`).

### 2. Inherit from `LLMAgent`, override `_call_llm`

We don't want a standalone `BaseAgent` for UI review — that would
duplicate ~80 lines of prompt rendering, JSON-mode request, Pydantic
validation, and retry-loop scaffolding. Instead `UiReviewerAgent`
overrides only `_call_llm` to:

1. Read `screenshot_paths` from `ctx.inputs`.
2. Build `ImageRef(path=...)` for each existing file (with a
   `ui.screenshot.missing` warning for stale paths).
3. **Short-circuit** to a fully-shaped `UiReport` JSON when there are
   no images at all (no LLM call, no token spend, no hallucinated
   review).
4. Otherwise build an `LLMRequest` with `images=images` on the user
   `LLMMessage` and `metadata={"vision": True}`.

The retry loop in `LLMAgent.execute` is unchanged — parse / schema
failures still trigger `AgentHallucinationError` retries.

### 3. Score is nullable

`UiReport.score: int | None = Field(default=None, ge=0, le=100)`. The
"skipped" case (no screenshots, or vision model returned a "I can't
grade this" verdict) is a first-class outcome, not an error. The report
writer renders `n/a (UI review skipped)` for `None` and `<n>/100`
otherwise.

### 4. `viewports_seen` back-fill

The LLM may forget to populate `viewports_seen` (or return `[]`). The
agent post-processes the validated payload to overwrite an empty list
with the actual viewport names from `screenshot_paths`. Tested in
`test_viewports_seen_back_filled`.

### 5. Report order

`Page Snapshot → Screenshots → SEO Review → UI Review → Methodology`.
Visual review reads as a summary of "what the page looks like" after
the structural facts; methodology closes with the disclaimer.

### 6. Token budget

Three viewports × ~1500 prompt tokens ≈ 4500 tokens. We bumped
`ui_reviewer.max_tokens` to 4096 (was 2048) so the response can carry
3+ detailed issues.

## End-to-end smoke

```bash
# Mock LLM (no API key) + null driver (no Chromium)
python -m dhrubo.commands.cli run-audit --url https://example.com/
# → runs/<timestamp>_https___example.com_/report.md
```

Generated `report.md` now contains:

- `## UI Review` section
- `Score: 70/100` (mock fallback)
- `Summary: Mock UI audit — set OPENAI_API_KEY for real visual analysis.`
- `Viewports reviewed: desktop, mobile, tablet`
- One `info` issue pointing the operator at `OPENAI_API_KEY`

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Image token cost (≈4500 prompt tokens per call) | Bumped `max_tokens` to 4096; documented in `models.yaml`. |
| 1×1 PNG from `NullDriver` confuses the vision model | Not blocking in M4 — the agent will return some "blank page" feedback. A `screenshot.too_small` warning is a follow-up. |
| Provider doesn't support `image_url` | The route already declares `vision: true`. Future providers should refuse early if they don't accept image parts (follow-up). |
| Retry loop floods the LLM with image tokens | Acceptable in M4 (max 3 attempts). Documented as known cost. |
| `viewports_seen` always empty | Agent back-fills from `screenshot_paths` if the LLM returns `[]`. |

## What's intentionally NOT in M4

- **Anthropic Claude vision provider** — deferred. OpenAI-compatible
  endpoints already work via `qwen3-vl-8b-instruct`. An
  `AnthropicProvider` (separate `messages` API with `image` content
  blocks) lands when there's a real need to call Claude.
- **`VisionCaptionTool`** — pre-LLM captioning for accessibility. The
  `vision_caption` permission entry is removed; vision is LLM-native.
- **Vector-store screenshot diffing** — flagged for M10.
- **Per-viewport individual reviews** — one LLM call sees all three
  viewports. Splitting per viewport is a follow-up optimization.
- **Image downscaling / re-encoding** — the agent attaches files as-is.
  Downscale to ≤1280px wide before sending is a follow-up; a TODO
  comment belongs in `UiReviewerAgent._call_llm`.
- **UI ↔ SEO issue cross-references** — the report writer concatenates
  sections but does not yet link "mobile screenshot shows broken H1" to
  the SEO issue. Add in a later report-writer pass.

## Migration to M5 (Performance Reviewer via Lighthouse)

The next milestone adds a third reviewer that calls the PageSpeed
Insights API and appends a Performance section to the report. Pattern
is identical to M4: subclass `LLMAgent`, register in the DAG between
`seo_review` and `ui_review`, extend the report writer with another
section. No engine, LLM contract, or image-pipeline changes required.
