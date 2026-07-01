import pytest
from dhrubo.llm import (
    LLMMessage,
    LLMRequest,
    MessageRole,
    MockProvider,
    OpenAICompatibleProvider,
)
from dhrubo.llm.interface import ILLMProvider


def test_provider_protocol_satisfied() -> None:
    assert isinstance(MockProvider(), ILLMProvider)
    assert isinstance(OpenAICompatibleProvider(), ILLMProvider)


@pytest.mark.asyncio
async def test_mock_provider_echo() -> None:
    p = MockProvider()
    req = LLMRequest(
        model="tiny",
        messages=[LLMMessage(role=MessageRole.USER, content="hi")],
    )
    out = await p.complete(req)
    assert out.content.startswith("[mock] ")
    assert p.calls == [req]


@pytest.mark.asyncio
async def test_mock_provider_json_payload() -> None:
    p = MockProvider()
    req = LLMRequest(
        model="tiny",
        messages=[LLMMessage(role=MessageRole.USER, content='!json:{"a": 1}')],
    )
    out = await p.complete(req)
    import json

    assert json.loads(out.content) == {"a": 1}


def test_openai_provider_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from dhrubo.core.errors import ProviderError

    async def _go() -> None:
        p = OpenAICompatibleProvider(api_key_env="OPENAI_API_KEY")
        with pytest.raises(ProviderError):
            await p.complete(
                LLMRequest(model="m", messages=[LLMMessage(role=MessageRole.USER, content="x")])
            )

    import asyncio

    asyncio.run(_go())
