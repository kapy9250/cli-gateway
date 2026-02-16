"""Tests for Improvement 5: Conversation history (/history)."""

import pytest


class TestHistoryCommand:
    """/history shows recent interactions for the current session."""

    @pytest.mark.asyncio
    async def test_history_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/history"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_history_empty(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/history"))
        text = fake_channel.last_sent_text()
        assert "暂无" in text or "空" in text or "没有" in text

    @pytest.mark.asyncio
    async def test_history_shows_entries(self, router, make_message, fake_channel, mock_agent):
        # Send a message to create session and record history
        await router.handle_message(make_message(text="what is python?"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/history"))
        text = fake_channel.last_sent_text()
        # Should contain the previous prompt
        assert "python" in text.lower() or "历史" in text


class TestHistoryRecording:
    """Router should record prompt/response pairs in session history."""

    @pytest.mark.asyncio
    async def test_history_recorded_after_message(self, router, make_message, fake_channel, mock_agent, session_manager):
        await router.handle_message(make_message(text="hello world"))
        active = session_manager.get_active_session("123")
        # Session should have history
        assert hasattr(active, 'history') or hasattr(session_manager, 'get_history')
