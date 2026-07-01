from typing import Any, ClassVar

import pytest
from dhrubo.agents.base_agent import AgentContext
from dhrubo.agents.llm_agent import LLMAgent
from dhrubo.llm import LLMRequest
from dhrubo.llm.interface import LLMCompletion
from dhrubo.llm.mock_provider import MockProvider
from pydantic import BaseModel


class Greeting(BaseModel):
    greeting: str
    target: str


class _HelloAgent(LLMAgent):
    role: ClassVar[str] = "_test_hello_agent"
    response_model: ClassVar[type[BaseModel]] = Greeting
    system_template: ClassVar[str] = "system"
    user_template: ClassVar[str] = "user {{ name }}"

    def build_variables(self, ctx: AgentContext) -> dict[str, Any]:
        return {"name": ctx.inputs.get("name", "world")}

    def build_user_prompt(self, ctx: AgentContext) -> str:
        return ""


def _provider(content: str) -> MockProvider:
    p = MockProvider()
    p.complete = _make_complete_with(content)  # type: ignore[assignment]
    return p


def _make_complete_with(content: str):
    async def _complete(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content=content, model=request.model)

    return _complete


async def test_llm_agent_happy_path() -> None:
    provider = _provider('{"greeting": "hi", "target": "world"}')
    agent = _HelloAgent()
    ctx = AgentContext(role=agent.role, inputs={"name": "world"}, llm=provider)
    res = await agent.execute(ctx)
    assert res.success is True
    # The default _to_result puts payload under "response".
    payload = res.outputs["response"]
    assert payload["greeting"] == "hi"
    assert payload["target"] == "world"


async def test_llm_agent_retries_on_bad_json() -> None:
    provider = MockProvider()

    call_count = {"n": 0}

    async def _flaky(request: LLMRequest) -> LLMCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCompletion(content="not json at all", model=request.model)
        return LLMCompletion(
            content='{"greeting": "hi", "target": "world"}', model=request.model
        )

    provider.complete = _flaky  # type: ignore[assignment]

    agent = _HelloAgent()
    ctx = AgentContext(role=agent.role, inputs={"name": "world"}, llm=provider)
    res = await agent.execute(ctx)
    assert res.success is True
    assert call_count["n"] == 2


async def test_llm_agent_fails_after_max_retries() -> None:
    from dhrubo.core.errors import AgentError, AgentHallucinationError

    provider = MockProvider()

    async def _always_bad(request: LLMRequest) -> LLMCompletion:
        return LLMCompletion(content="definitely not json", model=request.model)

    provider.complete = _always_bad  # type: ignore[assignment]
    agent = _HelloAgent()
    ctx = AgentContext(role=agent.role, inputs={"name": "world"}, llm=provider)
    with pytest.raises((AgentError, AgentHallucinationError)):
        await agent.execute(ctx)


async def test_llm_agent_requires_llm() -> None:
    from dhrubo.core.errors import AgentError

    agent = _HelloAgent()
    ctx = AgentContext(role=agent.role, inputs={"name": "world"}, llm=None)
    with pytest.raises(AgentError, match="no LLM"):
        await agent.execute(ctx)
