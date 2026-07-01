from pathlib import Path

import pytest
from dhrubo.config.loader import (
    build_settings,
    load_models_config,
    load_permissions_config,
    load_retry_policies,
)
from dhrubo.core.errors import ConfigError


def test_models_load_default(config_dir: Path) -> None:
    cfg = load_models_config(config_dir)
    assert cfg.default.name == "mock"
    assert cfg.default.model == "tiny"


def test_route_for_falls_back_to_default(config_dir: Path) -> None:
    cfg = load_models_config(config_dir)
    route = cfg.route_for("nonexistent_role")
    assert route.provider.model == "tiny"


def test_permissions_allow_deny(config_dir: Path) -> None:
    cfg = load_permissions_config(config_dir)
    assert cfg.allows("ui_reviewer", "browser") is True
    assert cfg.allows("ui_reviewer", "lighthouse") is False
    assert cfg.allows("unknown_agent", "browser") is False  # fail-closed


def test_retry_policies_parse(config_dir: Path) -> None:
    policies = load_retry_policies(config_dir)
    assert "default" in policies
    assert policies["default"].max_attempts == 2


def test_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    (tmp_path / "models.yaml").write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_models_config(tmp_path)


def test_build_settings_uses_config_dir(config_dir: Path) -> None:
    s = build_settings(config_dir=config_dir)
    assert s.config_directory == config_dir
    assert s.output.directory == Path("./output")
