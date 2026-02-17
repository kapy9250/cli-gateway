"""Tests for sys command audit sanitization."""

from __future__ import annotations

import io
import json
import logging

from core.commands.sys_cmd import _audit


class _Msg:
    channel = "telegram"
    chat_id = "chat-1"


class _Ctx:
    def __init__(self, logger):
        self.audit_logger = logger
        self.message = _Msg()
        self.user_id = "u-1"


def test_audit_redacts_sensitive_text_fields():
    stream = io.StringIO()
    logger = logging.getLogger("test.audit.sanitization")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(stream))

    ctx = _Ctx(logger)
    payload = {"op": "read_file", "stdout": "payload-sensitive", "path": "/etc/shadow"}
    result = {
        "ok": True,
        "text": "very-secret-content",
        "output": "docker secrets output",
        "nested": {"stderr": "inner-secret"},
        "safe_field": 123,
    }
    _audit(ctx, "read_file", payload, result)

    raw = stream.getvalue().strip()
    assert raw
    assert "very-secret-content" not in raw
    assert "docker secrets output" not in raw
    assert "inner-secret" not in raw
    assert "payload-sensitive" not in raw

    event = json.loads(raw)
    assert event["result"]["text"]["redacted"] is True
    assert event["result"]["output"]["redacted"] is True
    assert event["result"]["nested"]["stderr"]["redacted"] is True
    assert event["payload"]["stdout"]["redacted"] is True
    assert event["result"]["safe_field"] == 123
