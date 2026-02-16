"""Tests for utils/constants.py — constants integrity."""

import re
import pytest

from utils.constants import (
    MAX_ATTACHMENT_SIZE_BYTES,
    MAX_MESSAGE_LENGTH,
    SESSION_MARKER_RE,
    SESSION_MARKER_TEMPLATE,
    STREAM_UPDATE_INTERVAL,
)


class TestCommandRegistry:

    def test_all_expected_commands_registered(self):
        """Verify the command registry contains all 16 gateway commands."""
        from core.command_registry import registry

        expected = {
            "/start", "/help", "/agent", "/sessions", "/kill",
            "/current", "/switch", "/model", "/param", "/params", "/reset",
            "/files", "/download", "/cancel", "/name", "/history",
        }
        registered = {spec.name for spec in registry.list_all()}
        assert registered == expected

    def test_every_command_has_description(self):
        """Every registered command must have a non-empty description."""
        from core.command_registry import registry

        for spec in registry.list_all():
            assert spec.description, f"Command {spec.name} has no description"

    def test_every_command_has_callable_handler(self):
        """Every registered command must have a callable handler."""
        from core.command_registry import registry

        for spec in registry.list_all():
            assert callable(spec.handler), f"Command {spec.name} handler is not callable"


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
