"""YAML loaders and a factory that produces the runtime :class:`Settings`.

We separate *parsing* (this file) from *schema* (the pydantic models)
so adding new YAML files is purely additive.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dhrubo.config.models import ModelsConfig, RetryConfig
from dhrubo.config.permissions import PermissionsConfig
from dhrubo.config.settings import LoggingSettings, Settings
from dhrubo.core.errors import ConfigError
from dhrubo.core.logger import get_logger

_log = get_logger("config")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Failed to parse YAML at {path}",
            context={"path": str(path)},
            cause=exc,
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(
            "YAML root must be a mapping",
            context={"path": str(path)},
        )
    return data


def load_models_config(config_dir: Path) -> ModelsConfig:
    """Load ``models.yaml`` if present, else return defaults."""
    return ModelsConfig.model_validate(
        _read_yaml(config_dir / "models.yaml")
    )


def load_permissions_config(config_dir: Path) -> PermissionsConfig:
    """Load ``permissions.yaml`` if present, else return empty (fail-closed)."""
    return PermissionsConfig.model_validate(
        _read_yaml(config_dir / "permissions.yaml")
    )


def load_retry_policies(config_dir: Path) -> dict[str, RetryConfig]:
    """Load ``retry_policies.yaml`` as a mapping of ``name -> RetryConfig``."""
    raw = _read_yaml(config_dir / "retry_policies.yaml")
    policies: dict[str, RetryConfig] = {}
    for name, value in raw.items():
        try:
            policies[name] = RetryConfig.model_validate(value)
        except ValidationError as exc:
            raise ConfigError(
                f"Invalid retry policy '{name}'",
                context={"name": name},
                cause=exc,
            ) from exc
    return policies


def load_logging_config(config_dir: Path) -> LoggingSettings:
    """Load ``logging.yaml`` and return a typed :class:`LoggingSettings`."""
    raw = _read_yaml(config_dir / "logging.yaml")
    # Old config files used `json:`; translate it for backward compatibility.
    if isinstance(raw, dict) and "json" in raw and "json_logs" not in raw:
        raw["json_logs"] = raw.pop("json")
    return LoggingSettings.model_validate(raw)


def build_settings(
    *,
    config_dir: Path | None = None,
    base: Settings | None = None,
) -> Settings:
    """Assemble a complete :class:`Settings` by merging env + YAML defaults.

    Args:
        config_dir: Directory holding the YAML config files.
        base: Optional pre-loaded Settings (used by tests).

    Returns:
        Fully-validated :class:`Settings` instance.
    """
    settings = base or Settings()
    if config_dir is not None:
        settings = settings.model_copy(update={"config_directory": config_dir})

    # We deliberately do NOT bind the YAML-derived values into ``Settings``:
    # the YAML files describe *policies* consumed by other systems (the
    # model router, the workflow engine), not the runtime settings
    # themselves. Settings stay focused on deployment concerns.

    _log.debug(
        "settings.built",
        extra={
            "environment": settings.environment,
            "config_dir": str(settings.config_directory),
            "logging_level": settings.logging.level,
        },
    )
    return settings


# Re-export for callers that want the structured logger (used by setup_logging).
__all__ = [
    "build_settings",
    "load_logging_config",
    "load_models_config",
    "load_permissions_config",
    "load_retry_policies",
]


# Quiet down noisy import-time logs from external libs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
