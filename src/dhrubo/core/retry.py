"""Exponential-backoff retry helper.

Used by:
- :class:`dhrubo.agents.llm_agent.LLMAgent` (one internal retry loop; this
  is for higher-level "operation" retries)
- any tool / agent that wants the same primitive

The retry respects :class:`dhrubo.config.models.RetryConfig`.
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from dhrubo.config.models import RetryConfig
from dhrubo.core.errors import DhruboError
from dhrubo.core.logger import get_logger

_log = get_logger("retry")

T = TypeVar("T")

__all__ = [
    "DEFAULT_RETRY",
    "RetryConfig",
    "retry_async",
    "with_retry",
]


async def retry_async[T](
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryConfig,
    op_name: str = "operation",
    retriable: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Run ``op()`` with exponential backoff according to ``policy``.

    Args:
        op: Async callable to invoke.
        policy: A :class:`RetryConfig` describing the backoff schedule.
        op_name: A label used in log lines.
        retriable: Tuple of exception types that should trigger a retry.
            Anything else propagates immediately.

    Returns:
        Whatever ``op()`` returns on success.

    Raises:
        The last exception encountered, if all attempts fail.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await op()
        except retriable as exc:
            last_exc = exc
            if attempt >= policy.max_attempts:
                break
            delay = _compute_delay(policy, attempt)
            _log.warning(
                "retry.wait",
                extra={"op": op_name, "attempt": attempt, "delay_s": round(delay, 3), "error": str(exc)},
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _compute_delay(policy: RetryConfig, attempt: int) -> float:
    base = policy.initial_delay_seconds * (policy.backoff_multiplier ** (attempt - 1))
    capped = min(base, policy.max_delay_seconds)
    if policy.jitter:
        # Decorrelated jitter: uniform in [initial_delay, capped].
        return random.uniform(policy.initial_delay_seconds, capped)
    return capped


def with_retry(
    *,
    policy: RetryConfig,
    op_name: str | None = None,
    retriable: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator: wrap an async function with :func:`retry_async`."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        name = op_name or fn.__qualname__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await retry_async(
                lambda: fn(*args, **kwargs),
                policy=policy,
                op_name=name,
                retriable=retriable,
            )

        return wrapper

    return decorator


# Default policy if none is provided to the LLM/tool layer.
DEFAULT_RETRY = RetryConfig(
    max_attempts=3,
    initial_delay_seconds=1.0,
    max_delay_seconds=30.0,
    backoff_multiplier=2.0,
    jitter=True,
)


# Convenience type-check that retriable errors are sane.
def _check_retriable(retriable: tuple[type[BaseException], ...]) -> None:
    if not all(issubclass(e, BaseException) for e in retriable):
        raise DhruboError("retriable must be a tuple of exception types")


_check_retriable(retriable=(Exception,))  # smoke test
