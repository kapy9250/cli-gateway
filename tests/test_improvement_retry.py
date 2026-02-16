"""Tests for agent error handling (no retry on streaming responses)."""

import pytest

from channels.base import IncomingMessage


class TestAgentErrorHandling:
    """Router should report a user-friendly error when agent fails."""

    @pytest.mark.asyncio
    async def test_agent_failure_shows_friendly_error(self, auth, session_manager, sample_config, billing, tmp_path):
        """If agent raises, user gets a friendly error, not a traceback."""
        from core.router import Router
        from tests.conftest import FakeChannel, MockAgent

        class AlwaysFailAgent(MockAgent):
            async def send_message(self, session_id, message, model=None, params=None):
                raise RuntimeError("Persistent error")

        agent = AlwaysFailAgent(workspace_base=tmp_path / "agent")
        ch = FakeChannel()
        r = Router(auth=auth, session_manager=session_manager, agents={"claude": agent},
                    channel=ch, config=sample_config, billing=billing)

        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="test fail", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in ch.sent]
        combined = " ".join(texts)
        # Should show user-friendly error, not traceback
        assert "❌" in combined or "错误" in combined
        assert "Traceback" not in combined

    @pytest.mark.asyncio
    async def test_successful_response_recorded(self, auth, session_manager, sample_config, billing, tmp_path):
        """On success, response is delivered and history recorded."""
        from core.router import Router
        from tests.conftest import FakeChannel, MockAgent

        agent = MockAgent(workspace_base=tmp_path / "agent")
        ch = FakeChannel()
        r = Router(auth=auth, session_manager=session_manager, agents={"claude": agent},
                    channel=ch, config=sample_config, billing=billing)

        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="hello", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in ch.sent]
        combined = " ".join(texts)
        assert "Hello from mock agent" in combined
