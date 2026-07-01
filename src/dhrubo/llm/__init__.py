"""LLM provider abstraction.

All LLM calls flow through an implementation of :class:`ILLMProvider`.
The framework ships an :class:`OpenAICompatibleProvider` (covers OpenAI,
Azure-OpenAI, Ollama, vLLM, Groq, etc.) and a :class:`MockProvider` for
tests and ``--dry-run`` CLI mode.
"""

from dhrubo.llm.interface import (
    ILLMProvider,
    LLMCompletion,
    LLMMessage,
    LLMRequest,
    MessageRole,
)
from dhrubo.llm.mock_provider import MockProvider
from dhrubo.llm.openai_provider import OpenAICompatibleProvider
from dhrubo.llm.registry import LLMRegistry, llm_registry

__all__ = [
    "ILLMProvider",
    "LLMCompletion",
    "LLMMessage",
    "LLMRegistry",
    "LLMRequest",
    "MessageRole",
    "MockProvider",
    "OpenAICompatibleProvider",
    "llm_registry",
]
