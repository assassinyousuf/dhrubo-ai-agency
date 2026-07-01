"""LLM provider protocol + typed request/response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMMessage(BaseModel):
    """A single chat message."""

    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class LLMRequest(BaseModel):
    """A complete prompt to send to a model."""

    model: str
    messages: list[LLMMessage]
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0.0)
    response_format_json: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    """A model response, normalized across providers."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    raw: Any = None


@runtime_checkable
class ILLMProvider(Protocol):
    """Anything that can produce a :class:`LLMCompletion` for an :class:`LLMRequest`."""

    name: str

    async def complete(self, request: LLMRequest) -> LLMCompletion: ...

    async def aclose(self) -> None: ...
