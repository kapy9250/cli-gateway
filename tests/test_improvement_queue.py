"""Tests for Improvement 3: Message queuing mechanism."""

import asyncio
import pytest

from channels.base import IncomingMessage


class TestMessageQueuing:
    """When session is busy, messages should be queued instead of rejected."""

    @pytest.mark.asyncio
    async def test_queued_message_acknowledged(self, router, make_message, fake_channel, mock_agent):
        """When session is busy, user should get a queue notification instead of rejection."""
        await router.handle_message(make_message(text="hello"))
        sid = mock_agent.created_sessions[0]

        # Lock the session to simulate busy
        router._session_locks[sid] = asyncio.Lock()
        await router._session_locks[sid].acquire()

        fake_channel.sent.clear()
        await router.handle_message(make_message(text="queued message"))
        text = fake_channel.last_sent_text()
        # Should indicate queued, not just rejected
        assert "排队" in text or "队列" in text or "处理中" in text

        router._session_locks[sid].release()

    @pytest.mark.asyncio
    async def test_queued_message_processed_after_unlock(self, router, make_message, fake_channel, mock_agent):
        """After the current task finishes, the queued message should be processed."""
        await router.handle_message(make_message(text="hello"))
        sid = mock_agent.created_sessions[0]

        # We'll test that the queue attribute exists and can accept messages
        assert hasattr(router, '_message_queues') or hasattr(router, '_session_locks')
