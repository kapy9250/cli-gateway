"""Tests for Improvement 2: User-facing cancel command (/cancel)."""

import asyncio
import pytest

from channels.base import IncomingMessage


class TestCancelCommand:
    """/cancel stops the current agent operation."""

    @pytest.mark.asyncio
    async def test_cancel_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/cancel"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_cancel_not_busy(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/cancel"))
        text = fake_channel.last_sent_text()
        assert "没有正在执行" in text or "无任务" in text or "当前无" in text

    @pytest.mark.asyncio
    async def test_cancel_busy_session(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        sid = mock_agent.created_sessions[0]
        # Simulate busy state
        mock_agent.sessions[sid].is_busy = True

        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/cancel"))
        text = fake_channel.last_sent_text()
        assert "取消" in text or "✅" in text
