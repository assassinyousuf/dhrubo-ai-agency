import io
import json
import logging

import pytest
from dhrubo.core.logger import get_logger, setup_logging


def test_setup_logging_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO", force=True, stream=io.StringIO())
    setup_logging("INFO")  # let it write to stderr
    log = get_logger("test")
    log.info("hello", extra={"foo": 1})
    captured = capsys.readouterr()
    # The line should parse as JSON
    line = next(line for line in captured.err.splitlines() if line.startswith("{"))
    payload = json.loads(line)
    assert payload["msg"] == "hello"
    assert payload["foo"] == 1
    assert payload["logger"] == "dhrubo.test"


def test_get_logger_uses_framework_root() -> None:
    log = get_logger("foo.bar")
    assert log.name == "dhrubo.foo.bar"
    assert isinstance(log, logging.Logger)
