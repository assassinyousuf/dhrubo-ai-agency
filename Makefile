.PHONY: help install install-browser lint fmt typecheck test test-fast run-audit clean

PY     ?= python
PIP    ?= $(PY) -m pip
PKG    := dhrubo-ai-agency

help:
	@echo "Targets:"
	@echo "  install         - editable install with dev extras"
	@echo "  install-browser - install + Playwright Chromium"
	@echo "  lint            - ruff check"
	@echo "  fmt             - ruff format"
	@echo "  typecheck       - mypy"
	@echo "  test            - pytest with coverage"
	@echo "  test-fast       - pytest, no coverage"
	@echo "  run-audit       - run the CLI stub against example.com"
	@echo "  clean           - remove build artifacts and caches"

install:
	$(PIP) install -e ".[dev]"

install-browser:
	$(PIP) install -e ".[browser]"
	$(PY) -m playwright install chromium

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy $(PKG)

test:
	$(PY) -m pytest --cov=$(PKG) --cov-report=term-missing

test-fast:
	$(PY) -m pytest -q

run-audit:
	$(PY) -m dhrubo.commands.cli run-audit --url https://example.com --dry-run

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov/
	find . -name __pycache__ -type d -exec rm -rf {} +
