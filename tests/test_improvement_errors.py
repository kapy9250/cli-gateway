"""Tests for Improvement 8: User-friendly error messages."""

import pytest

from channels.base import IncomingMessage


class TestUserFriendlyErrors:
    """Error messages should be user-facing, not developer-facing."""

    @pytest.mark.asyncio
    async def test_no_python_traceback_in_output(
        self, auth, session_manager, sample_config, billing, mock_agent, fake_channel
    ):
        """Errors should never expose Python tracebacks to users."""
        from core.router import Router
        async def _raise_error(session_id, message, model=None, params=None):
            if False:
                yield ""  # keep async-generator shape expected by router
            raise Exception("Internal error: DB connection pool exhausted at line 42")

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
            text="trigger error", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in fake_channel.sent]
        combined = " ".join(texts)
        # Should NOT contain technical details
        assert "line 42" not in combined
        assert "DB connection" not in combined
        assert "Traceback" not in combined
        # Should contain user-friendly indicator
        assert "❌" in combined or "错误" in combined or "失败" in combined

    @pytest.mark.asyncio
    async def test_agent_not_found_error(self, router, make_message, fake_channel, mock_agent):
        """When the active agent disappears, show friendly error."""
        await router.handle_message(make_message(text="hello"))
        # Remove agent from router
        router.agents.clear()
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="second"))
        text = fake_channel.last_sent_text()
        assert "❌" in text
        assert "Agent" in text or "agent" in text
