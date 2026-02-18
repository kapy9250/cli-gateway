"""Tests for core/router.py — Session lifecycle management."""

import pytest

from channels.base import IncomingMessage


class TestAutoCreateSession:

    @pytest.mark.asyncio
    async def test_auto_create_session(self, router, make_message, fake_channel, session_manager):
        await router.handle_message(make_message(text="hello"))
        active = session_manager.get_active_session("123")
        assert active is not None

    @pytest.mark.asyncio
    async def test_auto_create_with_default_params(self, router, make_message, fake_channel, session_manager):
        await router.handle_message(make_message(text="hello"))
        active = session_manager.get_active_session("123")
        assert active.params == {"thinking": "low"}  # from default_params

    @pytest.mark.asyncio
    async def test_scope_workspace_partition(self, router, make_message, fake_channel, session_manager):
        await router.handle_message(make_message(text="dm hello", chat_id="dm-1", is_private=True))
        dm_scope = "telegram:dm:123"
        dm_session = session_manager.get_active_session_for_scope(dm_scope)
        assert dm_session is not None
        assert dm_session.work_dir is not None
        assert "telegram_user_123" in dm_session.work_dir

        await router.handle_message(make_message(text="group hello", chat_id="12345", is_private=False))
        group_scope = "telegram:chat:12345"
        group_session = session_manager.get_active_session_for_scope(group_scope)
        assert group_session is not None
        assert group_session.work_dir is not None
        assert "telegram_12345" in group_session.work_dir
        assert group_session.session_id != dm_session.session_id


class TestModelPreference:

    @pytest.mark.asyncio
    async def test_auto_create_with_model_preference(self, router, make_message, fake_channel, session_manager):
        # Set model preference before any session exists
        router._user_model_pref["123"] = "opus"
        await router.handle_message(make_message(text="hello"))
        active = session_manager.get_active_session("123")
        assert active.model == "opus"
        # Preference should be consumed (popped)
        assert "123" not in router._user_model_pref


class TestRecoverStaleSession:

    @pytest.mark.asyncio
    async def test_recover_stale_session(self, router, make_message, fake_channel, session_manager, mock_agent):
        # Create initial session
        await router.handle_message(make_message(text="first"))
        active = session_manager.get_active_session("123")
        old_sid = active.session_id

        # Simulate agent losing the session (e.g., restart)
        mock_agent.sessions.clear()

        # Update model/params to verify they are preserved
        session_manager.update_model(old_sid, "opus")
        session_manager.update_param(old_sid, "thinking", "high")

        # Next message should trigger recovery
        await router.handle_message(make_message(text="second"))
        new_active = session_manager.get_active_session("123")
        assert new_active is not None
        assert new_active.session_id == old_sid  # Session ID preserved
        assert new_active.model == "opus"  # Preserved
        assert new_active.params.get("thinking") == "high"  # Preserved


class TestCleanupOrphanBusy:

    @pytest.mark.asyncio
    async def test_cleanup_orphan_busy(self, router, make_message, fake_channel, mock_agent):
        # Create session
        await router.handle_message(make_message(text="first"))
        sid = mock_agent.created_sessions[0]
        session_info = mock_agent.sessions[sid]

        # Simulate orphan busy state
        session_info.is_busy = True

        # Mock is_process_alive to return False (process is dead)
        mock_agent.is_process_alive = lambda s: False

        async def async_kill(s):
            info = mock_agent.sessions.get(s)
            if info:
                info.is_busy = False

        mock_agent.kill_process = async_kill

        # Next message should clean up the orphan
        await router.handle_message(make_message(text="second"))
        # Should proceed without "busy" error — verify agent received second message
        assert len(mock_agent.messages_received) >= 2


class TestEmailSessionHint:

    @pytest.mark.asyncio
    async def test_email_session_hint_resume(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel
    ):
        from core.router import Router

        auth.add_user("user@test.com", "email")
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        # Create a session manually
        info = await mock_agent.create_session("user@test.com", "user@test.com")
        session_manager.create_session("user@test.com", "user@test.com", "claude", session_id=info.session_id)

        # Send email with session hint
        msg = IncomingMessage(
            channel="email", chat_id="user@test.com", user_id="user@test.com",
            text="continue", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
            session_hint=info.session_id,
        )
        await r.handle_message(msg)
        # Should resume the hinted session
        active = session_manager.get_active_session("user@test.com")
        assert active.session_id == info.session_id

    @pytest.mark.asyncio
    async def test_email_session_hint_invalid(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel
    ):
        from core.router import Router

        auth.add_user("user@test.com", "email")
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        # Send email with invalid hint → should create new session
        msg = IncomingMessage(
            channel="email", chat_id="user@test.com", user_id="user@test.com",
            text="new session", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
            session_hint="invalid-hint",
        )
        await r.handle_message(msg)
        active = session_manager.get_active_session("user@test.com")
        assert active is not None
        assert active.session_id != "invalid-hint"


class TestAgentNotAvailable:

    def test_router_with_no_agents_raises(self, auth, session_manager, sample_config, billing, fake_channel):
        """Router requires at least one agent — empty dict causes StopIteration in __init__."""
        from core.router import Router

        with pytest.raises(StopIteration):
            Router(
                auth=auth,
                session_manager=session_manager,
                agents={},
                channel=fake_channel,
                config=sample_config,
                billing=billing,
            )

    @pytest.mark.asyncio
    async def test_agent_mismatch_preference(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel
    ):
        """User prefers an agent that doesn't exist → error message."""
        from core.router import Router

        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )
        # Set user preference to nonexistent agent
        r._user_agent_pref["123"] = "nonexistent"

        msg = IncomingMessage(
            channel="telegram", chat_id="chat_1", user_id="123",
            text="hello", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r.handle_message(msg)
        text = fake_channel.last_sent_text()
        assert "不可用" in text or "❌" in text
