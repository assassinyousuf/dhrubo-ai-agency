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


class ImageRef(BaseModel):
    """A reference to an image attached to a chat message.

    Two equivalent forms are supported:
    - ``url``  — a public/data URL the model can fetch directly.
    - ``path`` — a local filesystem path the provider will base64-encode on
      the way out (see :mod:`dhrubo.tools.image_utils`).

    Exactly one of ``url`` or ``path`` must be set.
    """

    url: str | None = None
    path: str | None = None
    media_type: str | None = None  # "image/png", "image/jpeg", "image/webp"
    detail: str = "auto"           # OpenAI "low"|"high"|"auto"


class LLMMessage(BaseModel):
    """A single chat message."""

    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    images: list[ImageRef] = Field(default_factory=list)


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
