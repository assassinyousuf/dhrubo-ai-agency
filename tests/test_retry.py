import pytest
from dhrubo.config.models import RetryConfig
from dhrubo.core.retry import DEFAULT_RETRY, retry_async


class BoomError(Exception):
    pass


async def _flaky(attempts_until_success: list[int], fail_count: int):
    """Helper: returns a callable that fails ``fail_count`` times then succeeds."""
    state = {"calls": 0}

    async def _op():
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise BoomError(f"fail {state['calls']}")
        return "ok"

    return _op, state


async def test_retry_async_succeeds_after_failures() -> None:
    op, state = await _flaky([], fail_count=2)
    result = await retry_async(
        op,
        policy=RetryConfig(max_attempts=5, initial_delay_seconds=0.001, max_delay_seconds=0.01),
        op_name="flaky",
        retriable=(BoomError,),
    )
    assert result == "ok"
    assert state["calls"] == 3


async def test_retry_async_exhausts_attempts() -> None:
    op, state = await _flaky([], fail_count=10)
    with pytest.raises(BoomError):
        await retry_async(
            op,
            policy=RetryConfig(max_attempts=3, initial_delay_seconds=0.001, max_delay_seconds=0.01),
            op_name="flaky",
            retriable=(BoomError,),
        )
    assert state["calls"] == 3


async def test_retry_async_propagates_unrelated_error() -> None:
    async def _op():
        raise ValueError("not retriable")

    with pytest.raises(ValueError):
        await retry_async(
            _op,
            policy=RetryConfig(max_attempts=5, initial_delay_seconds=0.001, max_delay_seconds=0.01),
            op_name="op",
            retriable=(BoomError,),  # ValueError not retriable
        )


def test_default_retry_has_sane_defaults() -> None:
    assert DEFAULT_RETRY.max_attempts >= 1
    assert DEFAULT_RETRY.initial_delay_seconds > 0
