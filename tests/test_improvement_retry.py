"""Tests for agent error handling (no retry on streaming responses)."""

import pytest

from channels.base import IncomingMessage


class TestAgentErrorHandling:
    """Router should report a user-friendly error when agent fails."""

    @pytest.mark.asyncio
    async def test_agent_failure_shows_friendly_error(
        self, auth, session_manager, sample_config, billing, mock_agent, fake_channel
    ):
        """If agent raises, user gets a friendly error, not a traceback."""
        from core.router import Router
        async def _raise_error(session_id, message, model=None, params=None):
            if False:
                yield ""  # keep async-generator shape expected by router
            raise RuntimeError("Persistent error")

        mock_agent.send_message = _raise_error
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="test fail", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in fake_channel.sent]
        combined = " ".join(texts)
        # Should show user-friendly error, not traceback
        assert "❌" in combined or "错误" in combined
        assert "Traceback" not in combined

    @pytest.mark.asyncio
    async def test_successful_response_recorded(
        self, auth, session_manager, sample_config, billing, mock_agent, fake_channel
    ):
        """On success, response is delivered and history recorded."""
        from core.router import Router

        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="hello", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in fake_channel.sent]
        combined = " ".join(texts)
        assert "Hello from mock agent" in combined
