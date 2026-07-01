import pytest
from dhrubo.core.telemetry import (
    ConsoleTracer,
    NoopTracer,
    span,
)


def test_noop_tracer_does_not_fail() -> None:
    t = NoopTracer()
    s = t.start_span("op", k=1)
    s.set_attribute("x", 2)
    s.record_exception(ValueError("e"))
    s.end()


def test_console_tracer_records(capsys: pytest.CaptureFixture[str]) -> None:
    t = ConsoleTracer()
    with span(t, "demo", alpha=1):
        pass
    captured = capsys.readouterr()
    assert "span.end" in captured.err
    assert "demo" in captured.err
