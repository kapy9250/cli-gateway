"""Tests for core/router.py — Message forwarding and streaming."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from channels.base import Attachment, IncomingMessage


class TestForwardBasic:

    @pytest.mark.asyncio
    async def test_forward_basic_text(self, router, make_message, fake_channel, mock_agent):
        msg = make_message(text="hello world")
        await router.handle_message(msg)
        # Agent should have received a message
        assert len(mock_agent.messages_received) == 1
        # Channel should have sent a response
        assert len(fake_channel.sent) >= 1

    @pytest.mark.asyncio
    async def test_forward_creates_session_auto(self, router, make_message, fake_channel, session_manager):
        msg = make_message(text="first message")
        await router.handle_message(msg)
        # Session should have been auto-created
        active = session_manager.get_active_session("123")
        assert active is not None
        assert active.agent_name == "claude"


class TestStreamingResponse:

    @pytest.mark.asyncio
    async def test_forward_streaming_response(self, router, make_message, fake_channel, mock_agent):
        mock_agent.set_response(["chunk1", "chunk2", "chunk3"])
        await router.handle_message(make_message(text="test"))
        # Should have at least sent one message
        assert len(fake_channel.sent) >= 1

    @pytest.mark.asyncio
    async def test_forward_batch_response(self, router, make_message, fake_channel, mock_agent):
        fake_channel.supports_streaming = False
        mock_agent.set_response(["full response"])
        await router.handle_message(make_message(text="test"))
        # Batch mode: single send_text
        assert len(fake_channel.sent) >= 1
        assert "full response" in fake_channel.last_sent_text()


class TestAttachments:

    @pytest.mark.asyncio
    async def test_forward_with_attachments(self, router, make_message, fake_channel, mock_agent, tmp_path):
        # Create a temp file to be the attachment
        att_file = tmp_path / "test.txt"
        att_file.write_text("file content")
        att = Attachment(filename="test.txt", filepath=str(att_file), mime_type="text/plain", size_bytes=12)

        msg = make_message(text="check this file", attachments=[att])
        await router.handle_message(msg)
        assert len(mock_agent.messages_received) == 1
        _, prompt = mock_agent.messages_received[0]
        assert "附件" in prompt
        assert "test.txt" in prompt

    @pytest.mark.asyncio
    async def test_forward_oversized_attachment(self, router, make_message, fake_channel, mock_agent):
        att = Attachment(filename="big.bin", filepath="/tmp/big", mime_type="application/octet-stream", size_bytes=20 * 1024 * 1024)
        msg = make_message(text="check this", attachments=[att])
        await router.handle_message(msg)
        # Should warn about oversized attachment
        sent_texts = [t for _, t in fake_channel.sent]
        assert any("超过" in t or "限制" in t for t in sent_texts)


class TestSessionBusy:

    @pytest.mark.asyncio
    async def test_forward_session_busy(self, router, make_message, fake_channel, mock_agent):
        # Create session first
        msg1 = make_message(text="first")
        await router.handle_message(msg1)
        session_id = list(mock_agent.sessions.keys())[0]

        # Lock the session manually to simulate busy
        router._session_locks[session_id] = asyncio.Lock()
        await router._session_locks[session_id].acquire()

        # Second message should get "busy" response
        msg2 = make_message(text="second")
        await router.handle_message(msg2)
        sent_texts = [t for _, t in fake_channel.sent]
        assert any("处理中" in t for t in sent_texts)

        # Release lock
        router._session_locks[session_id].release()


class TestBilling:

    @pytest.mark.asyncio
    async def test_forward_records_billing(self, router, make_message, fake_channel, mock_agent, billing):
        await router.handle_message(make_message(text="test"))
        # Billing should have recorded
        total = billing.get_session_total(mock_agent.created_sessions[0])
        # MockAgent reports cost_usd=0.001
        assert total > 0


class TestEmailSessionLog:

    @pytest.mark.asyncio
    async def test_forward_email_session_log(self, auth, session_manager, mock_agent, sample_config, billing):
        """Email channel should trigger save_session_log."""
        from core.router import Router

        class FakeEmailChannel:
            supports_streaming = False
            sent = []
            async def send_text(self, chat_id, text):
                self.sent.append(text)
                return 1
            async def send_typing(self, chat_id):
                pass
            async def edit_message(self, chat_id, mid, text):
                pass
            async def cleanup_attachments(self, msg):
                pass
            def set_reply_session(self, chat_id, session_id):
                self._reply_session = (chat_id, session_id)
            def save_session_log(self, sender_addr, session_id, prompt, response):
                self._saved_log = (sender_addr, session_id, prompt, response)

        auth.add_user("user@test.com", "email")
        ch = FakeEmailChannel()
        r = Router(auth=auth, session_manager=session_manager, agents={"claude": mock_agent}, channel=ch, config=sample_config, billing=billing)

        msg = IncomingMessage(
            channel="email", chat_id="user@test.com", user_id="user@test.com",
            text="hello from email", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        assert hasattr(ch, '_saved_log')
        assert ch._saved_log[0] == "user@test.com"


class TestFormatConversion:

    def test_fmt_telegram_passthrough(self):
        from core.router import Router
        assert Router._fmt("telegram", "<b>bold</b>") == "<b>bold</b>"

    def test_fmt_discord_conversion(self):
        from core.router import Router
        result = Router._fmt("discord", "<b>bold</b> <code>code</code> &lt;tag&gt;")
        assert "**bold**" in result
        assert "`code`" in result
        assert "<tag>" in result


class TestUnauthorized:

    @pytest.mark.asyncio
    async def test_unauthorized_message(self, router, make_message, fake_channel):
        msg = make_message(text="hello", user_id="999")
        await router.handle_message(msg)
        assert "未授权" in fake_channel.last_sent_text()


class TestUnknownCommand:

    @pytest.mark.asyncio
    async def test_unknown_command_forwarded_to_agent(self, router, make_message, fake_channel, mock_agent):
        msg = make_message(text="/unknowncmd something")
        await router.handle_message(msg)
        # Should forward to agent, not error
        assert len(mock_agent.messages_received) >= 1
