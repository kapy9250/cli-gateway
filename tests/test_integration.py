"""End-to-end integration tests."""

import pytest

from channels.base import IncomingMessage


class TestFullWorkflow:

    @pytest.mark.asyncio
    async def test_create_message_destroy(self, router, make_message, fake_channel, session_manager, mock_agent):
        # 1. Send first message → auto-create session
        await router.handle_message(make_message(text="hello"))
        active = session_manager.get_active_session("123")
        assert active is not None
        sid = active.session_id

        # 2. Agent received the message
        assert len(mock_agent.messages_received) >= 1

        # 3. Channel sent response
        assert len(fake_channel.sent) >= 1

        # 4. Kill session
        await router.handle_message(make_message(text="/kill"))
        assert session_manager.get_active_session("123") is None


class TestMultiAgentSwitch:

    @pytest.mark.asyncio
    async def test_switch_claude_to_codex(self, multi_agent_router, make_message, fake_channel, session_manager, mock_agent, mock_codex_agent):
        r = multi_agent_router

        # Send message → creates claude session
        await r.handle_message(make_message(text="hello claude"))
        active = session_manager.get_active_session("123")
        assert active.agent_name == "claude"
        original_sid = active.session_id

        # Switch to codex
        await r.handle_message(make_message(text="/agent codex"))

        # Send message → keep same session but route to codex
        await r.handle_message(make_message(text="hello codex"))
        active = session_manager.get_active_session("123")
        assert active.agent_name == "codex"
        assert active.session_id == original_sid


class TestParameterIsolation:

    @pytest.mark.asyncio
    async def test_parameter_isolation(self, multi_agent_router, make_message, fake_channel, session_manager):
        r = multi_agent_router

        # Create claude session and set params
        await r.handle_message(make_message(text="hello"))
        await r.handle_message(make_message(text="/param thinking high"))

        claude_session = session_manager.get_active_session("123")
        assert claude_session.params.get("thinking") == "high"

        # Switch to codex
        await r.handle_message(make_message(text="/agent codex"))
        await r.handle_message(make_message(text="hello codex"))

        codex_session = session_manager.get_active_session("123")
        assert codex_session.agent_name == "codex"
        # Cross-agent unsupported params should be reset.
        assert codex_session.params.get("thinking") is None


class TestModelPreferenceApplied:

    @pytest.mark.asyncio
    async def test_model_preference_applied(self, router, make_message, fake_channel, session_manager):
        # Set preference before session creation
        await router.handle_message(make_message(text="/model opus"))
        # Now send a message to trigger session creation
        await router.handle_message(make_message(text="hello"))
        active = session_manager.get_active_session("123")
        assert active.model == "opus"


class TestMultipleUsersIsolation:

    @pytest.mark.asyncio
    async def test_multiple_users(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel
    ):
        from core.router import Router

        auth.add_user("u2", "telegram")
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        msg1 = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="hello from user 1", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        msg2 = IncomingMessage(
            channel="telegram", chat_id="c2", user_id="u2",
            text="hello from user 2", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )

        await r.handle_message(msg1)
        await r.handle_message(msg2)

        s1 = session_manager.get_active_session("123")
        s2 = session_manager.get_active_session("u2")
        assert s1 is not None
        assert s2 is not None
        assert s1.session_id != s2.session_id


class TestSessionPersistenceIntegration:

    @pytest.mark.asyncio
    async def test_persistence(
        self, auth, mock_agent, sample_config, billing, tmp_workspace, fake_channel
    ):
        from core.router import Router
        from core.session import SessionManager

        sm1 = SessionManager(workspace_base=tmp_workspace)
        r1 = Router(
            auth=auth,
            session_manager=sm1,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
        )

        # Create session via message
        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="123",
            text="persistent message", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        await r1.handle_message(msg)
        active_sid = sm1.get_active_session("123").session_id

        # New session manager loads from disk
        sm2 = SessionManager(workspace_base=tmp_workspace)
        loaded = sm2.get_active_session("123")
        assert loaded is not None
        assert loaded.session_id == active_sid
