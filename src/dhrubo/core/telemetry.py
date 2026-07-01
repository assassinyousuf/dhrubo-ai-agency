"""Lightweight tracing interface.

We deliberately avoid pulling in OpenTelemetry at this stage. The
:class:`Tracer` / :class:`Span` pair defines a thin contract that any
backend (OTLP, Jaeger, Console, in-memory) can implement later.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from dhrubo.core.logger import get_logger

_log = get_logger("telemetry")


class Span(Protocol):
    """A unit of work recorded by a :class:`Tracer`.

    Concrete implementations are free to record additional fields; this
    protocol only defines what the framework requires.
    """

    name: str

    def set_attribute(self, key: str, value: Any) -> None: ...
    def record_exception(self, exc: BaseException) -> None: ...
    def end(self) -> None: ...


class Tracer(Protocol):
    """Factory for :class:`Span` objects."""

    def start_span(self, name: str, **attributes: Any) -> Span: ...


@dataclass
class NoopSpan:
    """Span that does nothing. Used when tracing is disabled."""

    name: str
    _attributes: dict[str, Any] = field(default_factory=dict)

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self._attributes["exception"] = repr(exc)

    def end(self) -> None:
        # Emit a debug line so spans are still observable in logs.
        if self._attributes:
            _log.debug("span.end", extra={"span": self.name, "data": self._attributes})


@dataclass
class ConsoleSpan:
    """Span that logs its lifetime to the structured logger."""

    name: str
    start_time: float = field(default_factory=time.monotonic)
    _attributes: dict[str, Any] = field(default_factory=dict)

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self._attributes["error"] = repr(exc)

    def end(self) -> None:
        duration_ms = (time.monotonic() - self.start_time) * 1000.0
        _log.info(
            "span.end",
            extra={"span": self.name, "duration_ms": round(duration_ms, 2), **self._attributes},
        )


class ConsoleTracer:
    """A :class:`Tracer` that produces :class:`ConsoleSpan` instances."""

    def start_span(self, name: str, **attributes: Any) -> ConsoleSpan:
        span = ConsoleSpan(name=name)
        for k, v in attributes.items():
            span.set_attribute(k, v)
        _log.info("span.start", extra={"span": name, **attributes})
        return span


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Return the globally configured tracer.

    Defaults to :class:`NoopTracer`. Set via :func:`set_tracer` at startup
    (or in your application bootstrap) to enable real tracing.
    """
    global _tracer
    if _tracer is None:
        _tracer = _NoopTracer()
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    """Replace the global tracer."""
    global _tracer
    _tracer = tracer


class _NoopTracer:
    def start_span(self, name: str, **attributes: Any) -> NoopSpan:
        return NoopSpan(name=name)


# Convenience re-export so ``from dhrubo.core import NoopTracer`` works at module level.
NoopTracer = _NoopTracer


@contextlib.contextmanager
def span(tracer: Tracer | None = None, name: str = "span", **attributes: Any) -> Iterator[Span]:
    """Context-manager helper around :meth:`Tracer.start_span`."""
    t = tracer or get_tracer()
    s = t.start_span(name, **attributes)
    try:
        yield s
    except BaseException as exc:
        s.record_exception(exc)
        raise
    finally:
        s.end()
