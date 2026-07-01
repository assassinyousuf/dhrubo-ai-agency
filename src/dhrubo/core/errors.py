"""Typed exception hierarchy for the Dhrubo framework.

Every exception raised by the framework inherits from :class:`DhruboError`
so callers can catch the entire family with one ``except`` clause while
still being able to handle specific categories (agent vs. tool vs. workflow).
"""

from __future__ import annotations

from typing import Any


class DhruboError(Exception):
    """Base class for every error raised by the Dhrubo framework.

    Catch this to handle any framework-originated error. Production code
    should normally catch a more specific subclass.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context or {})
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.context:
            return f"{self.message} :: {self.context}"
        return self.message


class ConfigError(DhruboError):
    """Raised for invalid or missing configuration."""


class AgentError(DhruboError):
    """Base class for agent-level failures."""


class AgentHallucinationError(AgentError):
    """Raised when an agent's output fails QA validation (e.g. wrong schema)."""


class AgentTimeoutError(AgentError):
    """Raised when an agent exceeds its permitted execution time."""


class ToolError(DhruboError):
    """Base class for tool-execution failures."""


class ToolNotPermittedError(ToolError):
    """Raised when an agent attempts to use a tool it is not allowed to use."""


class WorkflowError(DhruboError):
    """Raised by the workflow engine (invalid DAG, missing dependency, etc.)."""


class ProviderError(DhruboError):
    """Raised by LLM providers (rate limits, auth, transport, etc.)."""
