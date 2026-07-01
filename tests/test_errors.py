from dhrubo.core.errors import (
    AgentError,
    ConfigError,
    DhruboError,
    ProviderError,
    ToolError,
    WorkflowError,
)


def test_hierarchy() -> None:
    assert issubclass(AgentError, DhruboError)
    assert issubclass(ToolError, DhruboError)
    assert issubclass(WorkflowError, DhruboError)
    assert issubclass(ProviderError, DhruboError)
    assert issubclass(ConfigError, DhruboError)


def test_context_and_cause() -> None:
    cause = ValueError("nope")
    err = ToolError("boom", context={"k": 1}, cause=cause)
    assert err.context == {"k": 1}
    assert err.__cause__ is cause
    assert "k" in str(err)
