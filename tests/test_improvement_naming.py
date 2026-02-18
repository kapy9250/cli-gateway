"""Tests for Improvement 4: Session naming (/name)."""

import pytest

from core.session import ManagedSession


class TestSessionNaming:
    """The /name command assigns a human-readable label to a session."""

    @pytest.mark.asyncio
    async def test_name_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/name my-project"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_name_set(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/name backend refactor"))
        text = fake_channel.last_sent_text()
        assert "✅" in text or "backend refactor" in text
        s = session_manager.get_session("s1")
        assert s.name == "backend refactor"

    @pytest.mark.asyncio
    async def test_name_no_arg(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        await router.handle_message(make_message(text="/name"))
        text = fake_channel.last_sent_text()
        assert "用法" in text or "name" in text.lower()

    @pytest.mark.asyncio
    async def test_sessions_shows_name(self, router, make_message, fake_channel, session_manager):
        session_manager.create_session("123", "chat_1", "claude", session_id="s1")
        session_manager.update_name("s1", "my-project")
        await router.handle_message(make_message(text="/sessions"))
        text = fake_channel.last_sent_text()
        assert "my-project" in text


class TestManagedSessionNameField:
    """ManagedSession should have a name field."""

    def test_name_default_none(self):
        s = ManagedSession(
            session_id="s1", user_id="u1", chat_id="c1",
            scope_id="u1",
            agent_name="claude", created_at=0.0, last_active=0.0,
        )
        assert s.name is None

    def test_name_set(self):
        s = ManagedSession(
            session_id="s1", user_id="u1", chat_id="c1",
            scope_id="u1",
            agent_name="claude", created_at=0.0, last_active=0.0,
            name="my-project",
        )
        assert s.name == "my-project"


class TestSessionManagerUpdateName:
    """SessionManager.update_name() persists the name."""

    def test_update_name(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        assert session_manager.update_name("s1", "test-name") is True
        assert session_manager.get_session("s1").name == "test-name"

    def test_update_name_nonexistent(self, session_manager):
        assert session_manager.update_name("nope", "x") is False

    def test_name_persisted(self, tmp_workspace):
        from core.session import SessionManager
        sm1 = SessionManager(workspace_base=tmp_workspace)
        sm1.create_session("u1", "c1", "claude", session_id="s1")
        sm1.update_name("s1", "persisted-name")

        sm2 = SessionManager(workspace_base=tmp_workspace)
        assert sm2.get_session("s1").name == "persisted-name"
