# Dhrubo AI Agency

Enterprise-grade autonomous AI Website Audit Agent platform.

> **Status:** v0.3 — Milestones 1, 2, and 3 complete.
> - **M1 (Repository Foundation):** Core architecture, config, `BaseAgent`, `Tool` ABCs, asynchronous DAG workflow engine, LLM providers (OpenAI-compatible and mock), and CLI scaffold.
> - **M2 (LLM & Vertical Slice):** Real crawler, SEO reviewer, report writer, and exporter; pipeline runs end-to-end with LLM integration and JSON-parse retry loops.
> - **M3 (Browser Subsystem):** `BrowserDriver` interface, `NullDriver`, `PlaywrightDriver` (optional extra), `ScreenshotTool`, and `ScreenshotAgent`. The crawler auto-promotes to Playwright with HTTP fallback. Added exponential backoff retry middleware.
>
> See `docs/MILESTONE_1.md`, `docs/MILESTONE_2_IMPLEMENTATION.md`, and `docs/MILESTONE_3_IMPLEMENTATION.md` for specific milestone details. The full architecture is described in `dhrubo_architecture.md`.

## What This Is

Dhrubo AI Agency is a modular, multi-agent framework that runs an ecosystem of specialized AI agents. Rather than relying on a single monolithic prompt or agent, it breaks complex workflows into narrow, specific tasks (e.g., crawler, screenshot, UI reviewer, SEO reviewer, report writer). 

These agents collaborate through a standardized asynchronous **Directed Acyclic Graph (DAG) Workflow Engine**, abstracted tool layers, and stateful memory banks. The first complete capability is a full **Website Audit Agent**, but the framework is architected to scale seamlessly to dozens of other capabilities, including business consultancy and automated proposal generation.

## Key Features & Guiding Principles

- **Multi-Agent Orchestration**: Specialization over monoliths. Agents own single responsibilities (e.g., `SEO Reviewer`, `UI Reviewer`).
- **Tool Abstraction**: Agents never interact directly with dependencies like Playwright or Lighthouse. They use normalized `Tool` interfaces, ensuring maximum maintainability.
- **Pluggable Workflows**: The DAG engine supports wave-based scheduling and asynchronous agent execution without rigid procedural code.
- **Enterprise Resiliency**: Built-in exponential backoff retry middleware (`core/retry.py`) and fail-soft fallbacks (e.g., falling back to HTTP if the Playwright browser crashes).
- **Configurable**: Model routing (picking cheap vs. smart models for specific tasks), retry policies, and permissions live in YAML configurations.
- **Observable**: Structured JSON logging and tracing interfaces are built in from day one.

## Install

```bash
# Core only (no heavy browser binaries)
pip install -e .

# With browser automation support (Playwright)
pip install -e ".[browser]"

# Everything you might want for local development (Browser, Performance, RAG, Vision)
pip install -e ".[browser,performance,pdf,anthropic,dev]"

# If using browser features, install the Chromium binary
playwright install chromium
```

## CLI Usage

```bash
# General help
dhrubo --help
dhrubo run-audit --help

# End-to-end audit (mock LLM if no OPENAI_API_KEY is set, uses HTTP fallback)
dhrubo run-audit --url https://example.com/

# Run with a real browser for JavaScript-rendered pages and multi-viewport screenshots
DHRUBO_USE_REAL_BROWSER=1 dhrubo run-audit --url https://example.com/
```

## Development

```bash
make install   # editable install + dev extras
make lint      # ruff + mypy (strict mode)
make test      # pytest suite
make run-audit # runs against a stub target (no real LLM yet)
```

### Outputs

The audit pipeline produces artifacts under `runs/<timestamp>_<host>/`. For example:
- `report.md` (Human-readable technical audit, including a **Screenshots** section listing desktop/mobile/tablet captures).
- `data.json` (Structured raw data).

## Repository Layout

```text
dhrubo-ai-agency/
├── agents/      # Specialized agent implementations (Planner, Coordinator, Reviewers)
├── commands/    # CLI entry points (Typer/Click)
├── config/      # YAML configurations (models, retry policies, permissions, logging)
├── core/        # Shared utilities, custom errors, structured logging, tracing
├── docs/        # Milestone tracking and extended implementation documentation
├── memory/      # State management (Session, Task, Vector Memory)
├── pipelines/   # Higher-level, cross-cutting pipelines
├── prompts/     # Version-controlled Jinja2 prompt templates
├── templates/   # Output rendering templates (HTML, MD, cold emails, proposals)
├── tools/       # Abstract + concrete tool implementations (Browser, Lighthouse)
├── workflows/   # Task orchestration (DAG Engine, Task Queue)
└── tests/       # Pytest test suite ensuring architectural contracts
```

See `dhrubo_architecture.md` (one level up) for the full system architecture rationale.
