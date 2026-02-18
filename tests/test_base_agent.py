"""Tests for agents/base.py — BaseAgent utility methods."""

import pytest
from pathlib import Path

from agents.base import BaseAgent, SessionInfo, UsageInfo


# Concrete subclass for testing non-abstract methods
class ConcreteAgent(BaseAgent):
    async def create_session(self, user_id, chat_id, session_id=None, work_dir=None, scope_dir=None):
        pass

    async def send_message(self, session_id, message):
        yield "test"

    async def cancel(self, session_id):
        pass

    async def destroy_session(self, session_id):
        pass

    def health_check(self, session_id):
        return {"alive": False}


@pytest.fixture
def agent(tmp_path):
    config = {
        "command": "test",
        "args_template": ["-p", "{prompt}", "--session-id", "{session_id}"],
        "models": {"small": "model-small", "large": "model-large"},
        "supported_params": {"model": "--model", "temp": "--temperature"},
    }
    return ConcreteAgent("test", config, tmp_path)


class TestInitWorkspace:

    def test_init_workspace(self, tmp_path):
        work_dir = tmp_path / "session_1"
        work_dir.mkdir()
        BaseAgent.init_workspace(work_dir)
        assert (work_dir / "user").is_dir()
        assert (work_dir / "ai").is_dir()
        assert (work_dir / "system").is_dir()
        assert (work_dir / "system" / "temp").is_dir()


class TestDirectoryHelpers:

    def test_get_user_upload_dir(self, tmp_path):
        d = BaseAgent.get_user_upload_dir(tmp_path)
        assert d.is_dir()
        assert d.name == "user"

    def test_get_ai_output_dir(self, tmp_path):
        d = BaseAgent.get_ai_output_dir(tmp_path)
        assert d.is_dir()
        assert d.name == "ai"

    def test_get_system_dir(self, tmp_path):
        d = BaseAgent.get_system_dir(tmp_path)
        assert d.is_dir()
        assert d.name == "system"

    def test_get_temp_dir(self, tmp_path):
        d = BaseAgent.get_temp_dir(tmp_path)
        assert d.is_dir()
        assert d.name == "temp"


class TestSafeFilename:

    def test_safe_filename_no_conflict(self, tmp_path):
        result = BaseAgent.safe_filename(tmp_path, "file.txt")
        assert result == tmp_path / "file.txt"

    def test_safe_filename_with_conflict(self, tmp_path):
        (tmp_path / "file.txt").touch()
        result = BaseAgent.safe_filename(tmp_path, "file.txt")
        assert result == tmp_path / "file_1.txt"

    def test_safe_filename_multiple_conflicts(self, tmp_path):
        (tmp_path / "file.txt").touch()
        (tmp_path / "file_1.txt").touch()
        (tmp_path / "file_2.txt").touch()
        result = BaseAgent.safe_filename(tmp_path, "file.txt")
        assert result == tmp_path / "file_3.txt"


class TestResolveModel:

    def test_resolve_model_with_alias(self, agent):
        assert agent._resolve_model("small") == "model-small"

    def test_resolve_model_no_alias(self, agent):
        assert agent._resolve_model(None) == ""
        assert agent._resolve_model("") == ""

    def test_resolve_model_unknown_passthrough(self, agent):
        assert agent._resolve_model("unknown") == "unknown"


class TestBuildArgs:

    def test_build_args_basic(self, agent):
        args = agent._build_args("hello world", "sid123")
        assert "-p" in args
        assert "hello world" in args
        assert "--session-id" in args
        assert "sid123" in args

    def test_build_args_with_model(self, agent):
        args = agent._build_args("hi", "sid", model="small")
        assert "--model" in args
        assert "model-small" in args

    def test_build_args_with_params(self, agent):
        args = agent._build_args("hi", "sid", params={"temp": "0.5"})
        assert "--temperature" in args
        assert "0.5" in args

    def test_build_args_with_model_and_params(self, agent):
        args = agent._build_args("hi", "sid", model="large", params={"temp": "0.8"})
        assert "model-large" in args
        assert "0.8" in args


class TestSessionManagement:

    def test_get_session_info(self, agent):
        import time
        si = SessionInfo(
            session_id="s1", agent_name="test", user_id="u1",
            work_dir=Path("/tmp"), created_at=time.time(), last_active=time.time(),
        )
        agent.sessions["s1"] = si
        assert agent.get_session_info("s1") is si
        assert agent.get_session_info("missing") is None

    def test_get_last_usage_pops(self, agent):
        agent._last_usage["s1"] = UsageInfo(input_tokens=10)
        usage = agent.get_last_usage("s1")
        assert usage.input_tokens == 10
        assert agent.get_last_usage("s1") is None  # popped

    def test_list_sessions(self, agent):
        import time
        for i in range(3):
            si = SessionInfo(
                session_id=f"s{i}", agent_name="test",
                user_id="u1" if i < 2 else "u2",
                work_dir=Path("/tmp"), created_at=time.time(), last_active=time.time(),
            )
            agent.sessions[f"s{i}"] = si

        assert len(agent.list_sessions()) == 3
        assert len(agent.list_sessions(user_id="u1")) == 2


class TestSessionInfoToDict:

    def test_to_dict(self):
        si = SessionInfo(
            session_id="s1", agent_name="test", user_id="u1",
            work_dir=Path("/tmp/ws"), created_at=1.0, last_active=2.0, pid=123, is_busy=True,
        )
        d = si.to_dict()
        assert d["session_id"] == "s1"
        assert d["work_dir"] == "/tmp/ws"
        assert d["pid"] == 123
        assert d["is_busy"] is True


class TestUsageInfoDefaults:

    def test_defaults(self):
        u = UsageInfo()
        assert u.input_tokens == 0
        assert u.cost_usd == 0.0
        assert u.model == ""
