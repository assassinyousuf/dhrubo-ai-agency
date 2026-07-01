"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from dhrubo.config.loader import build_settings


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """A temp config directory with minimal valid YAML files."""
    (tmp_path / "models.yaml").write_text(
        "default:\n  name: mock\n  model: tiny\n  temperature: 0\n  max_tokens: 16\n  timeout_seconds: 5\n",
        encoding="utf-8",
    )
    (tmp_path / "permissions.yaml").write_text(
        "agents:\n  - role: ui_reviewer\n    tools: [\"browser\"]\n",
        encoding="utf-8",
    )
    (tmp_path / "retry_policies.yaml").write_text(
        "default:\n  max_attempts: 2\n  initial_delay_seconds: 0.01\n",
        encoding="utf-8",
    )
    (tmp_path / "logging.yaml").write_text("level: WARNING\njson: false\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def settings(config_dir: Path):
    return build_settings(config_dir=config_dir)
