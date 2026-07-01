"""Provider registry: maps string names to provider factories."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dhrubo.llm.interface import ILLMProvider


class LLMRegistry:
    """Maps provider ``name`` strings to factory callables."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ILLMProvider]] = {}

    def register(self, name: str, factory: Callable[..., ILLMProvider]) -> None:
        if not name:
            raise ValueError("Provider name must be non-empty")
        self._factories[name] = factory

    def create(self, name: str, **kwargs: Any) -> ILLMProvider:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown LLM provider '{name}'. Known: {sorted(self._factories)}"
            ) from exc
        return factory(**kwargs)

    def names(self) -> list[str]:
        return sorted(self._factories)


llm_registry = LLMRegistry()
llm_registry.register("mock", lambda **kw: _make(MockProvider, kw))
llm_registry.register("openai", lambda **kw: _make(OpenAICompatibleProvider, kw))


def _make(cls: type[ILLMProvider], kwargs: dict[str, Any]) -> ILLMProvider:
    return cls(**kwargs)


# Late imports to avoid circulars (registry is imported by other llm modules).
from dhrubo.llm.mock_provider import MockProvider  # noqa: E402
from dhrubo.llm.openai_provider import OpenAICompatibleProvider  # noqa: E402
