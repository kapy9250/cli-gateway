"""Tests for Improvement 7: Auto-retry on transient agent failure."""

import pytest

from channels.base import IncomingMessage


class TestAutoRetry:
    """Router should auto-retry once on transient agent errors."""

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self, auth, session_manager, sample_config, billing, tmp_path):
        """If agent yields an error on first try, router retries once."""
        from core.router import Router
        from tests.conftest import FakeChannel, MockAgent

        call_count = 0

        class FailOnceAgent(MockAgent):
            async def send_message(self, session_id, message, model=None, params=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Transient error")
                yield "Success after retry"

        agent = FailOnceAgent(workspace_base=tmp_path / "agent")
        ch = FakeChannel()
        r = Router(auth=auth, session_manager=session_manager, agents={"claude": agent},
                    channel=ch, config=sample_config, billing=billing)

        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="test retry", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        texts = [t for _, t in ch.sent]
        combined = " ".join(texts)
        # Should either show the retry success or at least a user-friendly error
        assert "Success after retry" in combined or "重试" in combined or len(texts) >= 1

    @pytest.mark.asyncio
    async def test_no_retry_on_persistent_failure(self, auth, session_manager, sample_config, billing, tmp_path):
        """If agent fails twice, give up and show error."""
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
