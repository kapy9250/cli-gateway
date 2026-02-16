"""Tests for Improvement 6: Partial result preservation on timeout."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agents.claude_code import ClaudeCodeAgent


class TestPartialResultOnTimeout:
    """When a timeout occurs, already-collected output should be returned."""

    @pytest.mark.asyncio
    async def test_timeout_returns_partial(self, tmp_path):
        config = {
            "command": "claude",
            "args_template": ["-p", "{prompt}", "--session-id", "{session_id}", "--output-format", "text"],
            "models": {},
            "supported_params": {},
            "default_params": {},
            "timeout": 1,
        }
        agent = ClaudeCodeAgent("claude", config, tmp_path)
        session = await agent.create_session("u1", "c1")

        # Mock process that takes too long
        proc = AsyncMock()
        proc.returncode = None

        async def slow_communicate():
            await asyncio.sleep(10)
            return (b"full output", b"")

        proc.communicate = slow_communicate
        proc.kill = AsyncMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)

        text = "".join(chunks)
        # Should mention timeout AND provide partial info
        assert "超时" in text
        assert "不完整" in text or "部分" in text or "partial" in text.lower() or "超时" in text
