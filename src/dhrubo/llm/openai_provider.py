"""OpenAI-compatible provider implementation.

Works against any service that implements the OpenAI Chat Completions
API: OpenAI itself, Azure-OpenAI, Ollama (``/v1``), vLLM, LM Studio,
Groq, etc. Only ``base_url`` and the right API key env var need to change.
"""

from __future__ import annotations

import os
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)

from dhrubo.core.errors import ProviderError
from dhrubo.llm.interface import ILLMProvider, LLMCompletion, LLMMessage, LLMRequest


class OpenAICompatibleProvider:
    """Provider that talks the OpenAI Chat Completions protocol."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        organization: str | None = None,
    ) -> None:
        key = api_key or os.environ.get(api_key_env)
        if not key:
            # Defer the failure until .complete() is called so that
            # construction stays cheap and CLI --help works without secrets.
            self._client: AsyncOpenAI | None = None
            self._api_key_env = api_key_env
        else:
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=base_url,
                organization=organization,
            )
        self._api_key_env = api_key_env
        self._base_url = base_url

    async def complete(self, request: LLMRequest) -> LLMCompletion:
        if self._client is None:
            raise ProviderError(
                f"No API key configured (expected env var {self._api_key_env})",
                context={"env": self._api_key_env},
            )

        try:
            response = await self._client.chat.completions.create(  # type: ignore[call-overload]
                model=request.model,
                messages=[_to_openai_msg(m) for m in request.messages],
                temperature=request.temperature,
                max_completion_tokens=request.max_tokens,
                timeout=request.timeout_seconds,
                **(
                    {"response_format": {"type": "json_object"}}
                    if request.response_format_json
                    else {}
                ),
            )
        except (APITimeoutError, APIConnectionError) as exc:
            raise ProviderError(
                "LLM transport error",
                context={"provider": self.name, "model": request.model},
                cause=exc,
            ) from exc
        except AuthenticationError as exc:
            raise ProviderError(
                "LLM authentication failed",
                context={"provider": self.name},
                cause=exc,
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "LLM rate limit hit",
                context={"provider": self.name, "model": request.model},
                cause=exc,
            ) from exc
        except APIStatusError as exc:
            raise ProviderError(
                f"LLM API status error {exc.status_code}",
                context={"provider": self.name, "status": exc.status_code},
                cause=exc,
            ) from exc

        choice = response.choices[0]
        return LLMCompletion(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
            },
            finish_reason=getattr(choice, "finish_reason", "stop") or "stop",
            raw=response,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def _to_openai_msg(msg: LLMMessage) -> dict[str, Any]:
    """Translate our internal message into the SDK's dict shape."""
    if msg.images:
        from dhrubo.tools.image_utils import to_data_url

        parts: list[dict[str, Any]] = []
        if msg.content:
            parts.append({"type": "text", "text": msg.content})
        for ref in msg.images:
            if ref.url:
                url = ref.url
            elif ref.path:
                url = to_data_url(ref.path)
            else:
                raise ProviderError(
                    "ImageRef has neither url nor path",
                    context={"ref": ref.model_dump()},
                )
            part: dict[str, Any] = {
                "type": "image_url",
                "image_url": {"url": url, "detail": ref.detail},
            }
            parts.append(part)
        out: dict[str, Any] = {"role": msg.role.value, "content": parts}
    else:
        out = {"role": msg.role.value, "content": msg.content}
    if msg.name:
        out["name"] = msg.name
    if msg.tool_call_id:
        out["tool_call_id"] = msg.tool_call_id
    return out


# Static check: this class implements the protocol at runtime.
assert isinstance(OpenAICompatibleProvider(), ILLMProvider)
