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


# ---------------------------------------------------------------------------
# Multimodal translation (M4)
# ---------------------------------------------------------------------------


def test_llm_message_default_images_is_empty() -> None:
    """Backward-compat invariant: every existing call site keeps working."""
    msg = LLMMessage(role=MessageRole.USER, content="hi")
    assert msg.images == []


def test_openai_text_only_unchanged() -> None:
    from dhrubo.llm.openai_provider import _to_openai_msg

    msg = LLMMessage(role=MessageRole.USER, content="hi")
    out = _to_openai_msg(msg)
    assert out == {"role": "user", "content": "hi"}


def test_openai_image_url_part(tmp_path) -> None:
    from dhrubo.llm.interface import ImageRef
    from dhrubo.llm.openai_provider import _to_openai_msg
    from dhrubo.tools.image_utils import _PNG_1x1

    p = tmp_path / "a.png"
    p.write_bytes(_PNG_1x1)
    msg = LLMMessage(
        role=MessageRole.USER,
        content="what is this?",
        images=[ImageRef(path=str(p), detail="low")],
    )
    out = _to_openai_msg(msg)
    assert out["role"] == "user"
    assert isinstance(out["content"], list)
    assert out["content"][0] == {"type": "text", "text": "what is this?"}
    part = out["content"][1]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    assert part["image_url"]["detail"] == "low"


def test_openai_image_url_remote_passthrough() -> None:
    from dhrubo.llm.interface import ImageRef
    from dhrubo.llm.openai_provider import _to_openai_msg

    msg = LLMMessage(
        role=MessageRole.USER,
        content="x",
        images=[ImageRef(url="https://x/y.png")],
    )
    out = _to_openai_msg(msg)
    assert out["content"][1]["image_url"]["url"] == "https://x/y.png"


def test_openai_image_ref_with_neither_url_nor_path_raises() -> None:
    from dhrubo.core.errors import ProviderError
    from dhrubo.llm.interface import ImageRef
    from dhrubo.llm.openai_provider import _to_openai_msg

    msg = LLMMessage(role=MessageRole.USER, content="x", images=[ImageRef()])
    with pytest.raises(ProviderError):
        _to_openai_msg(msg)
