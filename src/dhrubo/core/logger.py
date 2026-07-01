"""Structured logging for the Dhrubo framework.

Uses :mod:`logging` under the hood but configures a JSON formatter so
log lines are machine-parseable for downstream observability (Loki,
CloudWatch, Datadog, etc.).

The default sink is ``stderr`` so log output does not fight with the Typer
CLI's ``stdout`` semantics — which keeps logs out of piped program output.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Root logger name for the framework. Library consumers can filter on this.
LOGGER_NAME = "dhrubo"

_level = logging.INFO
_configured = False


class JSONFormatter(logging.Formatter):
    """Render log records as a single-line JSON object."""

    # Static set of stdlib attributes that we don't want to forward as `extra`.
    _STDLIB_KEYS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "message",
            "asctime",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Forward any user-supplied `extra={...}` keys.
        for key, value in record.__dict__.items():
            if key not in self._STDLIB_KEYS and not key.startswith("_"):
                payload[key] = _safe(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _safe(value: Any) -> Any:
    """Best-effort JSON serializer for arbitrary log values."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def setup_logging(
    level: int | str = logging.INFO,
    *,
    stream: Any | None = None,
    force: bool = True,
) -> None:
    """Configure the ``dhrubo`` logger.

    Args:
        level: Logging level (string or int).
        stream: Output stream. Defaults to ``sys.stderr``.
        force: If True, replace existing handlers on the dhrubo logger.
    """
    global _level, _configured
    if isinstance(level, str):
        resolved: int = logging.getLevelName(level.upper())
        level = resolved

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger(LOGGER_NAME)
    if force:
        for h in list(logger.handlers):
            logger.removeHandler(h)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    _level = level
    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the framework logger."""
    if not _configured:
        setup_logging(_level)
    if name is None:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + ".") or name == LOGGER_NAME:
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
