# Milestone 1 — Repository Foundation

**Status:** ✅ Complete (July 2026)

## What was built

A compilable, type-safe, testable skeleton of the Dhrubo AI Agency framework.
All architectural contracts are in place; the next milestones fill in
concrete implementations.

### Modules

| Module | Purpose |
|---|---|
| `dhrubo/core` | Typed exception hierarchy, structured JSON logger, tracer interface |
| `dhrubo/config` | Pydantic-validated YAML loaders + env-var settings |
| `dhrubo/agents` | `BaseAgent` ABC + registry; agents declare role, inputs, outputs |
| `dhrubo/tools` | `Tool` ABC + registry; tools declare parameters via Pydantic |
| `dhrubo/workflows` | Async DAG engine, pluggable `TaskQueue`, website-audit pipeline |
| `dhrubo/llm` | `ILLMProvider` protocol + OpenAI-compatible + Mock implementations |
| `dhrubo/memory` | `SessionMemory` (async-safe dict; Redis-ready interface) |
| `dhrubo/commands` | Typer CLI with `run-audit` and `plan` commands |

### Tooling

- `pyproject.toml` (Hatchling, src/ layout, optional extras for browser/perf/pdf/vector/anthropic/broker)
- `ruff` configured (lint + format)
- `mypy` in strict mode (Pydantic plugin enabled)
- `pytest` with coverage and strict markers
- `Makefile` with `install`, `lint`, `typecheck`, `test`, `run-audit`, `clean`

## Architectural decisions worth recording

1. **Single top-level `dhrubo` package** under `src/dhrubo/`. Avoids
   clobbering common module names (`agents`, `tools`, `config`) on
   `sys.path` if the project is ever installed alongside other packages.

2. **`Tool` and `BaseAgent` are auto-registered at class-definition time**
   via `__init_subclass__`. No central "register all the things"
   function — adding a file is enough.

3. **Workflow engine uses wave-based scheduling**, not a single shared
   queue. Each iteration finds all tasks whose dependencies have
   completed and runs them concurrently. Cleaner DAG semantics and
   natural sync-barriers, with no broker dependency.

4. **Pluggable `TaskQueue` via a `Protocol`** with an `InProcessTaskQueue`
   default. A Redis/Arq backend slots in without engine changes.

5. **Permissions are fail-closed.** An agent without an entry in
   `permissions.yaml` may use zero tools.

6. **LLM providers are protocol-typed.** `OpenAICompatibleProvider` works
   against OpenAI, Azure-OpenAI, Ollama, vLLM, Groq, etc. — only
   `base_url` and the API key env var change.

7. **Heavy deps are optional extras.** `pip install dhrubo-ai-agency[browser]`
   is a deliberate choice so contributor / CI installs don't pull
   Chromium.

## Verification

```bash
make install         # editable install + dev extras
make lint            # ruff clean
make typecheck       # mypy strict — 0 errors across 28 files
make test            # 32 tests pass
make run-audit       # CLI stub works (plan-only mode)
```

## Next milestone (M2) preview

**M2: Browser Tool Foundation**
- Implement `BrowserDriver` interface (Playwright adapter behind it)
- Implement `tools/playwright_impl.py` concrete driver
- Implement retry policy enforcement
- Update browser tool's `params_model` to capture URL, selectors, etc.
