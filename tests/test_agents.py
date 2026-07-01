import asyncio
from typing import ClassVar

import pytest
from dhrubo.agents.base_agent import (
    AgentContext,
    AgentRegistry,
    AgentResult,
    BaseAgent,
)


class _StubAgent(BaseAgent):
    role: ClassVar[str] = "stub_test_agent"
    input_keys: ClassVar[tuple[str, ...]] = ("x",)
    output_keys: ClassVar[tuple[str, ...]] = ("y",)

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def execute(self, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("kaboom")
        return AgentResult.ok(self.role, y=ctx.inputs.get("x", 0) * 2)


def test_registry_autoregister() -> None:
    reg = AgentRegistry()
    reg.register(_StubAgent)
    assert reg.get("stub_test_agent") is _StubAgent
    assert "stub_test_agent" in reg.roles()


def test_execute_happy_path() -> None:
    agent = _StubAgent()
    ctx = AgentContext(role="stub_test_agent", inputs={"x": 21})
    result = asyncio.run(agent.execute(ctx))
    assert result.success is True
    assert result.outputs == {"y": 42}


def test_safe_execute_catches_exceptions() -> None:
    agent = _StubAgent(fail=True)
    ctx = AgentContext(role="stub_test_agent", inputs={"x": 1})
    result = asyncio.run(agent.safe_execute(ctx))
    assert result.success is False
    assert result.error is not None


def test_register_requires_role() -> None:
    class NoRole(BaseAgent):
        async def execute(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover
            return AgentResult.ok("norole")

    reg = AgentRegistry()
    with pytest.raises(ValueError):
        reg.register(NoRole)
