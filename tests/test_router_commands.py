"""Tests for core/router.py — Gateway command handling."""

import pytest

from channels.base import IncomingMessage


class TestStartCommand:

    @pytest.mark.asyncio
    async def test_start_command(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/start"))
        assert len(fake_channel.sent) == 1
        assert "启动" in fake_channel.last_sent_text() or "Gateway" in fake_channel.last_sent_text()


class TestHelpCommand:

    @pytest.mark.asyncio
    async def test_help_command(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/help"))
        assert len(fake_channel.sent) == 1
        text = fake_channel.last_sent_text()
        assert "命令" in text or "help" in text.lower()


class TestAgentCommand:

    @pytest.mark.asyncio
    async def test_agent_show_current(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/agent"))
        text = fake_channel.last_sent_text()
        assert "agent" in text.lower() or "Agent" in text

    @pytest.mark.asyncio
    async def test_agent_switch(self, multi_agent_router, make_message, fake_channel, session_manager):
        await multi_agent_router.handle_message(make_message(text="/agent codex"))
        text = fake_channel.last_sent_text()
        assert "codex" in text.lower()
        assert "切换" in text or "✅" in text
        current = session_manager.get_active_session("123")
        assert current is None

    @pytest.mark.asyncio
    async def test_agent_switch_keeps_existing_session(
        self, multi_agent_router, make_message, fake_channel, session_manager
    ):
        await multi_agent_router.handle_message(make_message(text="hello"))
        before = session_manager.get_active_session("123")
        assert before is not None

        await multi_agent_router.handle_message(make_message(text="/agent codex"))
        after = session_manager.get_active_session("123")
        assert after is not None
        assert after.session_id == before.session_id
        assert after.agent_name == "codex"

    @pytest.mark.asyncio
    async def test_agent_switch_invalid(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/agent nonexistent"))
        text = fake_channel.last_sent_text()
        assert "❌" in text or "未找到" in text


class TestSessionsCommand:

    @pytest.mark.asyncio
    async def test_sessions_empty(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/sessions"))
        assert "暂无" in fake_channel.last_sent_text()

    @pytest.mark.asyncio
    async def test_sessions_list(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/sessions"))
        text = fake_channel.last_sent_text()
        assert "s1" in text


class TestCurrentCommand:

    @pytest.mark.asyncio
    async def test_current_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/current"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "无" in text
        assert "版本" in text

    @pytest.mark.asyncio
    async def test_current_show(self, router, make_message, fake_channel, session_manager):
        router.config.setdefault("runtime", {})["version"] = "git:test123"
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/current"))
        text = fake_channel.last_sent_text()
        assert "s1" in text
        assert "claude" in text
        assert "git:test123" in text

    @pytest.mark.asyncio
    async def test_current_no_session_shows_scope_agent_preference(self, router, make_message, fake_channel):
        message = make_message(text="/current")
        scope_id = router.get_scope_id(message)
        router._set_scope_agent(scope_id, "gemini")
        await router.handle_message(message)
        text = fake_channel.last_sent_text()
        assert "下一条将使用" in text
        assert "gemini" in text


class TestSwitchCommand:

    @pytest.mark.asyncio
    async def test_switch_session(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        session_manager.create_session("123", "chat_1", "claude", session_id="s2")
        await router.handle_message(make_message(text="/switch s1"))
        text = fake_channel.last_sent_text()
        assert "s1" in text
        assert "✅" in text or "切换" in text

    @pytest.mark.asyncio
    async def test_switch_invalid(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/switch nonexistent"))
        text = fake_channel.last_sent_text()
        assert "❌" in text or "不存在" in text

    @pytest.mark.asyncio
    async def test_switch_no_arg(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/switch"))
        text = fake_channel.last_sent_text()
        assert "用法" in text or "session_id" in text


class TestKillCommand:

    @pytest.mark.asyncio
    async def test_kill_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/kill"))
        assert "无活跃" in fake_channel.last_sent_text() or "无" in fake_channel.last_sent_text()

    @pytest.mark.asyncio
    async def test_kill_session(self, router, make_message, fake_channel, session_manager, mock_agent):
        # Create a session via agent + manager
        info = await mock_agent.create_session("123", "chat_1")
        session_manager.create_session("123", "chat_1", "claude", session_id=info.session_id)
        await router.handle_message(make_message(text="/kill"))
        text = fake_channel.last_sent_text()
        assert "销毁" in text or "🗑️" in text
        assert session_manager.get_active_session("123") is None


class TestModelCommand:

    @pytest.mark.asyncio
    async def test_model_list(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/model"))
        text = fake_channel.last_sent_text()
        assert "sonnet" in text or "opus" in text or "模型" in text

    @pytest.mark.asyncio
    async def test_model_switch_with_session(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/model opus"))
        text = fake_channel.last_sent_text()
        assert "opus" in text
        assert "✅" in text or "切换" in text
        assert session_manager.get_session("s1").model == "opus"

    @pytest.mark.asyncio
    async def test_model_switch_no_session_saves_preference(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/model opus"))
        text = fake_channel.last_sent_text()
        assert "opus" in text
        # Preference should be stored
        assert router._user_model_pref.get("telegram:dm:123") == "opus"

    @pytest.mark.asyncio
    async def test_model_invalid(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/model nonexistent"))
        text = fake_channel.last_sent_text()
        assert "❌" in text or "不存在" in text


class TestParamCommand:

    @pytest.mark.asyncio
    async def test_param_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/param"))
        assert "❌" in fake_channel.last_sent_text() or "无活跃" in fake_channel.last_sent_text()

    @pytest.mark.asyncio
    async def test_param_list(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1", params={"thinking": "low"})
        await router.handle_message(make_message(text="/param"))
        text = fake_channel.last_sent_text()
        assert "thinking" in text

    @pytest.mark.asyncio
    async def test_param_set(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1", params={})
        await router.handle_message(make_message(text="/param thinking high"))
        text = fake_channel.last_sent_text()
        assert "✅" in text
        assert session_manager.get_session("s1").params["thinking"] == "high"

    @pytest.mark.asyncio
    async def test_param_invalid_key(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/param invalid_key value"))
        assert "❌" in fake_channel.last_sent_text()

    @pytest.mark.asyncio
    async def test_param_missing_value(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/param thinking"))
        assert "用法" in fake_channel.last_sent_text()


class TestParamsCommand:

    @pytest.mark.asyncio
    async def test_params_show(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1", model="opus", params={"thinking": "high"})
        await router.handle_message(make_message(text="/params"))
        text = fake_channel.last_sent_text()
        assert "opus" in text
        assert "thinking" in text

    @pytest.mark.asyncio
    async def test_params_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/params"))
        assert "❌" in fake_channel.last_sent_text() or "无活跃" in fake_channel.last_sent_text()


class TestResetCommand:

    @pytest.mark.asyncio
    async def test_reset(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1", model="opus", params={"thinking": "high"})
        await router.handle_message(make_message(text="/reset"))
        text = fake_channel.last_sent_text()
        assert "✅" in text or "重置" in text
        s = session_manager.get_session("s1")
        assert s.model == "sonnet"  # default
        assert s.params == {"thinking": "low"}  # default

    @pytest.mark.asyncio
    async def test_reset_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/reset"))
        assert "❌" in fake_channel.last_sent_text() or "无活跃" in fake_channel.last_sent_text()


class TestKapyFormat:

    @pytest.mark.asyncio
    async def test_kapy_help(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="kapy help"))
        text = fake_channel.last_sent_text()
        assert "命令" in text or "help" in text.lower()

    @pytest.mark.asyncio
    async def test_kapy_model_switch(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="kapy model opus"))
        text = fake_channel.last_sent_text()
        assert "opus" in text

    @pytest.mark.asyncio
    async def test_kapy_empty_stripped_to_bare_word(self, router, make_message, fake_channel, mock_agent):
        # "kapy " gets stripped to "kapy" which doesn't start with "kapy "
        # so it's forwarded to agent as plain text
        await router.handle_message(make_message(text="kapy "))
        # The stripped text "kapy" goes to _forward_to_agent
        assert len(mock_agent.messages_received) >= 1

    @pytest.mark.asyncio
    async def test_kapy_with_empty_subcommand(self, router, make_message, fake_channel):
        # "kapy  " with double space: stripped to "kapy" → forwarded to agent
        # This is expected behavior — bare "kapy" word is treated as normal text
        await router.handle_message(make_message(text="kapy  "))
        assert len(fake_channel.sent) >= 1
