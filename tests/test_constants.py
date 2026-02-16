"""Tests for utils/constants.py — constants integrity."""

import re
import pytest

from utils.constants import (
    GATEWAY_COMMANDS,
    MAX_ATTACHMENT_SIZE_BYTES,
    MAX_MESSAGE_LENGTH,
    SESSION_MARKER_RE,
    SESSION_MARKER_TEMPLATE,
    STREAM_UPDATE_INTERVAL,
)


class TestGatewayCommands:

    def test_gateway_commands_complete(self):
        expected = {
            "/start", "/help", "/agent", "/sessions", "/kill",
            "/current", "/switch", "/model", "/param", "/params", "/reset",
            "/files", "/download", "/cancel", "/name", "/history",
        }
        assert GATEWAY_COMMANDS == expected

    def test_gateway_commands_is_frozenset(self):
        assert isinstance(GATEWAY_COMMANDS, frozenset)


class TestSessionMarker:

    def test_session_marker_regex_matches(self):
        marker = SESSION_MARKER_TEMPLATE.format(session_id="abcdef12")
        m = SESSION_MARKER_RE.search(marker)
        assert m is not None
        assert m.group(1) == "abcdef12"

    def test_session_marker_regex_rejects_short(self):
        assert SESSION_MARKER_RE.search("<!-- clawdbot-session:abc -->") is None


class TestConstants:

    def test_max_attachment_size(self):
        assert MAX_ATTACHMENT_SIZE_BYTES == 10 * 1024 * 1024

    def test_message_lengths(self):
        assert MAX_MESSAGE_LENGTH["telegram"] == 4096
        assert MAX_MESSAGE_LENGTH["discord"] == 2000
        assert MAX_MESSAGE_LENGTH["email"] is None

    def test_stream_interval(self):
        assert STREAM_UPDATE_INTERVAL == 2.0
