"""Typed schemas for YAML config files.

These Pydantic models are the single source of truth for configuration.
Loaders parse YAML into these models; the rest of the framework consumes
typed objects, never raw dicts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderKind = Literal["openai", "anthropic", "mock"]


class ProviderConfig(BaseModel):
    """An LLM provider — its model and per-request generation params."""

    name: ProviderKind = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0.0)


class ModelRoute(BaseModel):
    """Maps an agent role to the provider/model it should use.

    This is the routing layer mentioned in the architecture document —
    we can send heavy logic to one model and cheap classification to another
    without changing any agent code.
    """

    role: str
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    vision: bool = False  # set True if the agent receives image inputs


class ModelsConfig(BaseModel):
    """Top-level ``models.yaml`` schema."""

    default: ProviderConfig = Field(default_factory=ProviderConfig)
    routes: list[ModelRoute] = Field(default_factory=list)

    def route_for(self, role: str) -> ModelRoute:
        """Return the route for ``role`` or the default if none is configured."""
        for route in self.routes:
            if route.role == role:
                return route
        return ModelRoute(role=role, provider=self.default)


class RetryConfig(BaseModel):
    """Exponential backoff settings for a single named operation."""

    max_attempts: int = Field(default=3, ge=1)
    initial_delay_seconds: float = Field(default=1.0, gt=0.0)
    max_delay_seconds: float = Field(default=30.0, gt=0.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    jitter: bool = True
