"""Tests for core/session.py — SessionManager module."""

import time
from pathlib import Path

import pytest

from core.session import ManagedSession, SessionManager


class TestCreateSession:

    def test_create_session_basic(self, session_manager):
        s = session_manager.create_session("u1", "c1", "claude", session_id="abc12345")
        assert s.session_id == "abc12345"
        assert s.user_id == "u1"
        assert s.chat_id == "c1"
        assert s.agent_name == "claude"
        assert s.model is None
        assert s.params == {}

    def test_create_with_model_and_params(self, session_manager):
        s = session_manager.create_session("u1", "c1", "claude", model="opus", params={"thinking": "high"})
        assert s.model == "opus"
        assert s.params == {"thinking": "high"}

    def test_create_auto_generates_id(self, session_manager):
        s = session_manager.create_session("u1", "c1", "claude")
        assert len(s.session_id) == 8
        assert all(c in "0123456789abcdef" for c in s.session_id)

    def test_create_sets_active(self, session_manager):
        s = session_manager.create_session("u1", "c1", "claude", session_id="s1")
        active = session_manager.get_active_session("u1")
        assert active is not None
        assert active.session_id == "s1"


class TestGetSession:

    def test_get_session(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        s = session_manager.get_session("s1")
        assert s is not None
        assert s.session_id == "s1"

    def test_get_session_not_found(self, session_manager):
        assert session_manager.get_session("nonexistent") is None


class TestActiveSession:

    def test_get_active_session(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        active = session_manager.get_active_session("u1")
        assert active.session_id == "s1"

    def test_get_active_no_session(self, session_manager):
        assert session_manager.get_active_session("u1") is None

    def test_latest_create_is_active(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        session_manager.create_session("u1", "c1", "claude", session_id="s2")
        active = session_manager.get_active_session("u1")
        assert active.session_id == "s2"


class TestListSessions:

    def test_list_user_sessions(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        session_manager.create_session("u1", "c1", "codex", session_id="s2")
        session_manager.create_session("u2", "c2", "claude", session_id="s3")
        sessions = session_manager.list_user_sessions("u1")
        assert len(sessions) == 2

    def test_list_all_sessions(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        session_manager.create_session("u2", "c2", "codex", session_id="s2")
        all_sessions = session_manager.list_all_sessions()
        assert len(all_sessions) == 2


class TestSwitchSession:

    def test_switch_session(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        session_manager.create_session("u1", "c1", "codex", session_id="s2")
        assert session_manager.switch_session("u1", "s1") is True
        active = session_manager.get_active_session("u1")
        assert active.session_id == "s1"

    def test_switch_session_wrong_user(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        assert session_manager.switch_session("u2", "s1") is False

    def test_switch_nonexistent(self, session_manager):
        assert session_manager.switch_session("u1", "nope") is False


class TestTouch:

    def test_touch_updates_last_active(self, session_manager):
        s = session_manager.create_session("u1", "c1", "claude", session_id="s1")
        old_time = s.last_active
        time.sleep(0.01)
        session_manager.touch("s1")
        assert session_manager.get_session("s1").last_active > old_time

    def test_touch_nonexistent(self, session_manager):
        # Should not raise
        session_manager.touch("nonexistent")


class TestDestroySession:

    def test_destroy_session(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        destroyed = session_manager.destroy_session("s1")
        assert destroyed is not None
        assert destroyed.session_id == "s1"
        assert session_manager.get_session("s1") is None
        assert session_manager.get_active_session("u1") is None

    def test_destroy_nonexistent(self, session_manager):
        assert session_manager.destroy_session("nope") is None


class TestUpdateModelAndParams:

    def test_update_model(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        assert session_manager.update_model("s1", "opus") is True
        assert session_manager.get_session("s1").model == "opus"

    def test_update_model_nonexistent(self, session_manager):
        assert session_manager.update_model("nope", "opus") is False

    def test_update_param(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1")
        assert session_manager.update_param("s1", "thinking", "high") is True
        assert session_manager.get_session("s1").params["thinking"] == "high"

    def test_reset_params(self, session_manager):
        session_manager.create_session("u1", "c1", "claude", session_id="s1", params={"a": "1", "b": "2"})
        assert session_manager.reset_params("s1", {"a": "default"}) is True
        assert session_manager.get_session("s1").params == {"a": "default"}


class TestMaxSessionsEviction:

    def test_max_sessions_eviction(self, tmp_workspace):
        sm = SessionManager(workspace_base=tmp_workspace, max_sessions_per_user=2)
        s1 = sm.create_session("u1", "c1", "claude", session_id="s1")
        time.sleep(0.01)
        s2 = sm.create_session("u1", "c1", "claude", session_id="s2")
        time.sleep(0.01)
        s3 = sm.create_session("u1", "c1", "claude", session_id="s3")
        # s1 should be evicted (oldest)
        assert sm.get_session("s1") is None
        assert sm.get_session("s2") is not None
        assert sm.get_session("s3") is not None


class TestPersistence:

    def test_persistence_across_instances(self, tmp_workspace):
        sm1 = SessionManager(workspace_base=tmp_workspace)
        sm1.create_session("u1", "c1", "claude", session_id="persist1", model="opus", params={"k": "v"})

        sm2 = SessionManager(workspace_base=tmp_workspace)
        s = sm2.get_session("persist1")
        assert s is not None
        assert s.model == "opus"
        assert s.params == {"k": "v"}
        assert sm2.get_active_session("u1").session_id == "persist1"

    def test_persistence_scope_active_pointer(self, tmp_workspace):
        sm1 = SessionManager(workspace_base=tmp_workspace)
        sm1.create_session(
            "u1",
            "c1",
            "claude",
            session_id="persist_scope",
            scope_id="telegram:dm:u1",
            work_dir=str(tmp_workspace / "claude" / "telegram_user_u1" / "sess_persist_scope"),
        )

        sm2 = SessionManager(workspace_base=tmp_workspace)
        scoped = sm2.get_active_session_for_scope("telegram:dm:u1")
        assert scoped is not None
        assert scoped.session_id == "persist_scope"


class TestCleanupInactive:

    def test_cleanup_inactive_sessions(self, tmp_workspace):
        sm = SessionManager(workspace_base=tmp_workspace, cleanup_inactive_after_hours=0)
        # cleanup_inactive_after_hours=0 means disabled
        sm.create_session("u1", "c1", "claude", session_id="s1")
        assert sm.cleanup_inactive_sessions() == 0

    def test_cleanup_stale_sessions(self, tmp_workspace):
        sm = SessionManager(workspace_base=tmp_workspace, cleanup_inactive_after_hours=1)
        s = sm.create_session("u1", "c1", "claude", session_id="s1")
        # Manually age the session
        s.last_active = time.time() - 7200  # 2 hours ago
        sm._save()
        count = sm.cleanup_inactive_sessions()
        assert count == 1
        assert sm.get_session("s1") is None


class TestGenerateSessionId:

    def test_generate_session_id_format(self):
        sid = SessionManager.generate_session_id()
        assert len(sid) == 8
        assert all(c in "0123456789abcdef" for c in sid)

    def test_generate_session_id_unique(self):
        ids = {SessionManager.generate_session_id() for _ in range(100)}
        assert len(ids) == 100  # All unique


class TestManagedSessionPostInit:

    def test_params_default_to_empty_dict(self):
        s = ManagedSession(
            session_id="s1", user_id="u1", chat_id="c1",
            scope_id="u1",
            agent_name="claude", created_at=0.0, last_active=0.0,
        )
        assert s.params == {}
