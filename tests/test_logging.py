"""Tests for the shared logging configuration."""

import json
import logging

from common.logging_config import _JsonFormatter, configure_logging


def test_json_formatter_outputs_json():
    fmt = _JsonFormatter()
    record = logging.LogRecord(
        "svc", logging.INFO, "f.py", 1, "hello %s", ("world",), None
    )
    out = json.loads(fmt.format(record))
    assert out["level"] == "INFO"
    assert out["logger"] == "svc"
    assert out["message"] == "hello world"


def test_configure_logging_selects_formatter():
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging("json")
        assert isinstance(root.handlers[0].formatter, _JsonFormatter)
        configure_logging("text")
        assert not isinstance(root.handlers[0].formatter, _JsonFormatter)
    finally:
        root.handlers[:] = saved  # don't disturb pytest's log capture
