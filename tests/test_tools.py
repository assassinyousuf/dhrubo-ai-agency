import asyncio
from typing import ClassVar

import pytest
from dhrubo.core.errors import ToolError
from dhrubo.tools.tool_interface import (
    Tool,
    ToolContext,
    ToolParameter,
    ToolResult,
)
from pydantic import BaseModel


class _Params(BaseModel):
    n: int


class _GoodTool(Tool[_Params]):
    name: ClassVar[str] = "test_good"
    description: ClassVar[str] = "Adds 1"
    parameters: ClassVar[tuple[ToolParameter, ...]] = (ToolParameter("n", "int"),)
    params_model: ClassVar[type[BaseModel]] = _Params

    async def run(self, params: _Params, ctx: ToolContext) -> ToolResult:
        return ToolResult.ok(self.name, data=params.n + 1)


def test_tool_registry_autoregister() -> None:
    from dhrubo.tools.tool_interface import tool_registry

    assert "test_good" in tool_registry.names()


def test_tool_runs_validated_params() -> None:
    from dhrubo.tools.tool_interface import tool_registry

    tool = tool_registry.instantiate("test_good")
    result = asyncio.run(tool.safe_run({"n": 4}, ToolContext(requester_role="test")))
    assert result.success is True
    assert result.data == 5


def test_tool_invalid_params_returns_failed_result() -> None:
    from dhrubo.tools.tool_interface import tool_registry

    tool = tool_registry.instantiate("test_good")
    result = asyncio.run(tool.safe_run({"n": "not-a-number"}, ToolContext(requester_role="t")))
    assert result.success is False
    assert "Invalid" in (result.error or "")


def test_tool_without_params_model_rejects_validation() -> None:
    class _Bare(Tool):  # type: ignore[type-arg]
        name = "bare_tool"

        async def run(self, params, ctx):  # type: ignore[override]
            return ToolResult.ok(self.name, data=None)

    bare = _Bare()
    with pytest.raises(ToolError):
        bare.validate_params({"n": 1})
