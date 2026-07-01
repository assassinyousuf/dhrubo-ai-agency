# Dhrubo AI Agency - Milestone 2: Data Collection Pipeline Specification

**Status:** Proposed  
**Author:** Principal AI Architect  
**Project:** Dhrubo AI Agency Core Platform  
**Target:** Website Data Collection Pipeline  

## Executive Summary
Milestone 1 successfully established the modular architecture, orchestration, and LLM foundations of Dhrubo AI Agency. The core tenet of Milestone 2 is: **Intelligence is bottlenecked by the quality of its sensory input.** Before any AI reasoning or auditing can take place, the platform must become an expert deterministic data collector. 

This milestone focuses entirely on building a robust, fault-tolerant, and comprehensive Website Data Collection Pipeline. This pipeline will act as the "eyes and ears" of all future AI agents, transforming raw internet domains into highly structured, reusable, and deterministic audit datasets. No AI analysis occurs in this milestone.

---

## 1. Milestone 2 Architecture

The Data Collection Architecture is built around an asynchronous pipeline utilizing Playwright for browser interactions. It acts as an ETL (Extract, Transform, Load) system for websites.

**Core Principles:**
- **Deterministic**: No LLM calls. Everything is rule-based and script-driven.
- **Fault-Tolerant**: Network requests fail. Browsers crash. The system must expect and recover from this.
- **Provider-Agnostic**: Abstraction layers must sit in front of Lighthouse, axe-core, and Playwright.
- **Idempotent Storage**: Running the collection twice on the same timestamp should yield the exact same directory structure.

**Data Flow:**
`Target URL` → `Crawling & Discovery` → `Distributed Collection (HTML, Screenshots, A11y, Perf)` → `Aggregation` → `Structured Audit Artifact`

---

## 2. Repository Modifications
The `dhrubo-ai-agency` structure from Milestone 1 remains intact, but we are enriching the `tools/` and `workflows/` layers significantly to support heavy data collection.

---

## 3. New Folders
- `src/dhrubo/tools/browser/`: Playwright-specific implementation logic, context managers, and browser lifecycle.
- `src/dhrubo/tools/auditors/`: Wrappers for Lighthouse, axe-core, Wappalyzer.
- `src/dhrubo/data_models/`: Pydantic schemas for the collected data.
- `src/dhrubo/storage/`: Logic for managing the output audit artifact directory structure and writing raw files.

---

## 4. New Python Modules

- `src/dhrubo/tools/browser/playwright_manager.py`: Manages browser lifecycle, contexts, and isolated sessions.
- `src/dhrubo/tools/browser/crawler.py`: Logic for BFS/DFS navigation, URL normalization, and `robots.txt` enforcement.
- `src/dhrubo/tools/browser/screenshot_manager.py`: Viewport management, full-page vs. component stitching.
- `src/dhrubo/tools/browser/dom_extractor.py`: Logic for extracting raw HTML, rendered DOM, and JSON-LD metadata.
- `src/dhrubo/tools/auditors/lighthouse_runner.py`: Subprocess/API wrapper for Lighthouse execution.
- `src/dhrubo/tools/auditors/axe_runner.py`: Injector for axe-core JS and result parser.
- `src/dhrubo/tools/auditors/tech_detector.py`: Pluggable interface currently utilizing a basic regex/header scanner (to be swapped with Wappalyzer).
- `src/dhrubo/storage/artifact_builder.py`: Manages the physical creation of the audit directory.

---

## 5. Agent Responsibilities
Since this milestone is deterministic, agents act purely as functional orchestrators rather than LLM reasoners.
- **`website_crawler.py`**: Initiates the `crawler.py` tool. Manages the scope (how deep to crawl, respecting rate limits).
- **`screenshot_agent.py`**: Executes the `screenshot_manager.py`. Identifies elements (e.g., `<nav>`, `<footer>`) to screenshot specifically.

---

## 6. Tool Responsibilities

- **Playwright Integration**: 
  - *Lifecycle*: Headless by default. Browsers are launched per audit, contexts per page.
  - *Isolation*: Use isolated browser contexts to clear cookies/local storage between sessions.
  - *Timeouts*: Strict 30s navigation timeouts, fallback to `domcontentloaded`.
- **Website Discovery**: 
  - *Traversal*: Recursive link extraction, bounding crawls to the original domain.
  - *Deduplication*: Maintain a `visited_urls.set` using normalized URLs (strip fragments and tracking query params).
- **HTML & Asset Collection**:
  - *Headless DOM*: Serialize the DOM *after* JavaScript execution.
  - *Assets*: Intercept network requests via Playwright to download CSS, JS, and image assets.
- **Accessibility & Performance**:
  - *axe-core*: Inject `axe.min.js` into the page evaluate context, run, and export JSON.
  - *Lighthouse*: Run via local Node.js CLI process against the target URL, saving raw JSON.

---

## 7. Workflow Updates
Update `website_audit_pipeline.py` to become a strict DAG of data collection:
1. `Init Context` -> Create Audit Directory.
2. `Discovery Phase` -> Crawl and identify unique URLs up to a limit (e.g., `MAX_PAGES=5`).
3. `Parallel Collection Phase` -> For each discovered URL, fan out parallel tasks:
   - Capture Screenshots
   - Extract HTML/DOM
   - Run axe-core
4. `Global Collection Phase` -> Run Lighthouse (runs on main domain), Technology Detection.
5. `Finalize` -> Write schemas, aggregate logs.

---

## 8. Data Schemas (Pydantic Models)
To ensure type safety, the following Pydantic schemas must be created in `data_models/`:
- `AuditMetadata`: timestamp, target_url, crawler_config, total_pages_crawled.
- `PageData`: url, canonical_url, title, meta_description, http_status.
- `ScreenshotData`: filepath, device_type, viewport_size, component_type (e.g., "hero").
- `LighthouseRawData`: strict pass-through of Lighthouse JSON structure.
- `AxeRawData`: strict pass-through of axe-core JSON structure.

---

## 9. JSON Schema for Collected Artifacts
The `audit.json` manifest sitting at the root of the artifact directory will adhere to:

```json
{
  "audit_id": "uuid4",
  "target_url": "https://example.com",
  "timestamp": "ISO8601",
  "pages": [
    {
      "url": "https://example.com/about",
      "paths": {
        "raw_html": "html/about_raw.html",
        "dom_snapshot": "html/about_rendered.html",
        "screenshots": {
          "desktop": "screenshots/desktop/about_full.png",
          "mobile": "screenshots/mobile/about_full.png",
          "hero": "screenshots/components/about_hero.png"
        },
        "axe_report": "axe/about_axe.json"
      }
    }
  ],
  "global_reports": {
    "lighthouse": "lighthouse/report.json",
    "technologies": "technologies/detected.json"
  }
}
```

---

## 10. Naming Conventions
- **Audit Directory**: `output/{domain}_{YYYYMMDD_HHMMSS}/` (e.g., `output/example_com_20260701_120000/`)
- **Files**: Convert URLs to safe filenames (e.g., `/about-us` -> `about_us`).
- **Screenshots**: `{safe_path}_{device_type}_{component}.png` (e.g., `about_us_mobile_hero.png`)

---

## 11. Error Recovery Strategy
- **Network Timeouts**: If a page fails to load after 30s, log a warning, mark the page as `FAILED` in the manifest, and continue crawling other pages. Do not crash the entire audit.
- **Lighthouse Failures**: If Lighthouse crashes (e.g., Node.js memory limit), fallback to saving the standard error log to `lighthouse/error.log` and proceed.
- **Resumable Audits**: (Future proofing) If an audit crashes midway, a `state.json` file in the audit directory tracks completed tasks, allowing the pipeline to resume from the last successful URL rather than restarting.

---

## 12. Retry Strategy
Using the `Tenacity` library (or similar):
- **Page Navigation**: 3 retries, exponential backoff (2s, 4s, 8s).
- **Element Screenshots**: 2 retries (often fails due to lazy loading or shifting layouts).
- **Network Interception**: No retries for individual assets (CSS/JS). If it fails, log and continue.

---

## 13. Parallel Execution Opportunities
- **Page-Level Parallelism**: Crawling, Screenshots, and HTML extraction for different URLs should be fanned out across multiple async Playwright contexts simultaneously.
- **Module-Level Parallelism**: Axe-core and DOM extraction can happen concurrently within the same page context via `asyncio.gather()`.
- **Constraint**: Lighthouse must run serially or completely independently to prevent CPU/Network saturation from skewing performance metrics.

---

## 14. Future Scalability Considerations
- **Storage**: Currently writing to local disk (`output/`). The `ArtifactBuilder` must be an interface so it can easily be swapped to an S3/GCS bucket writer for cloud deployments.
- **Distributed Crawling**: The Task Queue architecture allows us to move from local `asyncio` to Celery/Redis in the future, running thousands of browsers across Kubernetes pods.
- **Authentication**: The Playwright manager must support loading predefined `.har` files or cookie injects to audit sites behind login walls in the future.

---

## 15. Testing Strategy
- **Mock Web Server**: Spin up a local `pytest-httpserver` serving static HTML files with specific layouts to test crawler behavior without hitting the real internet.
- **Deterministic Validation**: Assert that crawling a 3-page local site produces exactly 3 HTML files, 9 screenshots (Desktop/Mobile/Tablet), and 1 valid `audit.json` manifest.
- **Failure Injection**: Force a 500 server error on the mock server and verify the Error Recovery strategy handles it gracefully.

---

## 16. Documentation Updates
- Update `README.md` to include Playwright installation dependencies (`playwright install chromium`).
- Add a new document `docs/DATA_COLLECTION.md` explaining the `audit.json` schema to front-end or AI engineers who will consume this data.

---

## 17. Risks
- **Bot Mitigation**: Cloudflare/Akamai blocking the headless crawler. *Mitigation:* Ensure User-Agent spoofing and stealth configurations are supported in the Playwright context config.
- **Disk Space**: Storing DOMs, raw HTML, and high-res screenshots for hundreds of pages consumes significant disk space. *Mitigation:* Enforce strict crawling depth limits (`MAX_DEPTH=2`, `MAX_PAGES=10`) as defaults.
- **Zombie Processes**: Headless browsers can leak and hang in memory if Python crashes. *Mitigation:* Strict `try/finally` context managers ensuring `browser.close()` is always executed.

---

## 18. Implementation Order
1. **Infrastructure**: Implement Pydantic data schemas (`src/dhrubo/data_models/`) and the `ArtifactBuilder` (`src/dhrubo/storage/`).
2. **Browser Core**: Build the `PlaywrightManager` and ensure contexts can be created/destroyed cleanly.
3. **Discovery**: Implement the `Crawler` logic with duplicate prevention and `robots.txt` handling.
4. **Data Extractors**: Implement `DOMExtractor` and `ScreenshotManager`.
5. **Auditors**: Wrap `axe-core` and `Lighthouse`.
6. **Orchestration**: Wire tools into the `website_audit_pipeline.py`.
7. **Testing**: Build mock web server tests and validate idempotency.

---

## 19. Definition of Done
Milestone 2 is considered complete when:
- The CLI command `puku run-collection https://example.com` executes successfully.
- An enterprise-grade artifact directory is generated containing all specified raw HTML, DOM, screenshots (desktop/mobile/tablet + components), and metadata.
- Both `lighthouse` and `axe-core` JSON outputs are successfully captured and saved.
- The pipeline gracefully handles a 404 and a network timeout without crashing.
- `pytest` suite passes with the mock web server tests verifying crawler logic.
- The system executes with zero LLM API calls.
