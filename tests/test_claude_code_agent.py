"""Tests for agents/claude_code.py — ClaudeCodeAgent."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.claude_code import ClaudeCodeAgent
from agents.base import UsageInfo


class FakeRemoteClient:
    def __init__(self, response: dict):
        self.response = dict(response)
        self.calls = []

    async def execute(self, user_id: str, action: dict, grant_token: str = None):
        self.calls.append({"user_id": str(user_id), "action": dict(action or {})})
        return dict(self.response)


@pytest.fixture
def claude_config():
    return {
        "command": "claude",
        "args_template": ["-p", "{prompt}", "--session-id", "{session_id}", "--output-format", "text"],
        "models": {"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-6"},
        "default_model": "sonnet",
        "supported_params": {"model": "--model", "thinking": "--thinking", "max_turns": "--max-turns"},
        "default_params": {"thinking": "low"},
        "timeout": 10,
    }


@pytest.fixture
def agent(tmp_path, claude_config):
    return ClaudeCodeAgent("claude", claude_config, tmp_path)


def _make_mock_process(stdout_data: bytes = b"", stderr_data: bytes = b"", returncode: int = 0):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_data, stderr_data))
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    return proc


class TestCreateSession:

    @pytest.mark.asyncio
    async def test_create_session(self, agent):
        info = await agent.create_session("u1", "c1")
        assert info.session_id is not None
        assert info.agent_name == "claude"
        assert info.user_id == "u1"
        assert info.work_dir.exists()
        assert (info.work_dir / "user").is_dir()
        assert (info.work_dir / "ai").is_dir()
        assert info.session_id in agent.sessions


class TestSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_success(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({
            "result": "Hello world!",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.001,
            "duration_ms": 500,
        })
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test prompt"):
                chunks.append(chunk)

        assert "Hello world!" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_send_message_usage_extraction(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({
            "result": "ok",
            "usage": {"input_tokens": 200, "output_tokens": 100, "cache_read_input_tokens": 10},
            "total_cost_usd": 0.005,
            "duration_ms": 1000,
            "modelUsage": {"claude-opus-4-6": {}},
        })
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async for _ in agent.send_message(session.session_id, "test"):
                pass

        usage = agent.get_last_usage(session.session_id)
        assert usage is not None
        assert usage.input_tokens == 200
        assert usage.output_tokens == 100
        assert usage.cost_usd == 0.005
        assert usage.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_send_message_first_call_uses_session_id(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({"result": "ok"})
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            async for _ in agent.send_message(session.session_id, "test"):
                pass
            # Check args contain --session-id
            call_args = mock_exec.call_args
            all_args = list(call_args[0])
            assert "--session-id" in all_args

    @pytest.mark.asyncio
    async def test_send_message_resume_uses_resume(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({"result": "ok"})
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        # First call
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async for _ in agent.send_message(session.session_id, "first"):
                pass

        # Second call should use --resume
        mock_proc2 = _make_mock_process(stdout_data=json_response.encode(), returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc2) as mock_exec:
            async for _ in agent.send_message(session.session_id, "second"):
                pass
            all_args = list(mock_exec.call_args[0])
            assert "--resume" in all_args
            assert "--session-id" not in all_args

    @pytest.mark.asyncio
    async def test_send_message_with_model(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({"result": "ok"})
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            async for _ in agent.send_message(session.session_id, "test", model="opus"):
                pass
            all_args = list(mock_exec.call_args[0])
            assert "--model" in all_args
            assert "claude-opus-4-6" in all_args

    @pytest.mark.asyncio
    async def test_send_message_with_params(self, agent):
        session = await agent.create_session("u1", "c1")
        json_response = json.dumps({"result": "ok"})
        mock_proc = _make_mock_process(stdout_data=json_response.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            async for _ in agent.send_message(session.session_id, "test", params={"thinking": "high"}):
                pass
            all_args = list(mock_exec.call_args[0])
            assert "--thinking" in all_args
            assert "high" in all_args

    @pytest.mark.asyncio
    async def test_send_message_uses_remote_system_client_when_configured(self, tmp_path, claude_config):
        remote_payload = {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "result": "remote-result",
                    "usage": {"input_tokens": 3, "output_tokens": 4},
                    "total_cost_usd": 0.01,
                    "duration_ms": 12,
                }
            ),
            "stderr": "",
        }
        remote = FakeRemoteClient(remote_payload)
        agent = ClaudeCodeAgent(
            "claude",
            claude_config,
            tmp_path,
            runtime_mode="session",
            instance_id="user-main",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test prompt"):
                chunks.append(chunk)

        assert "remote-result" in "".join(chunks)
        assert not mock_exec.called
        assert len(remote.calls) == 1
        action = remote.calls[0]["action"]
        assert action["op"] == "agent_cli_exec"
        assert action["agent"] == "claude"
        assert action["mode"] == "session"
        assert action["instance_id"] == "user-main"

    @pytest.mark.asyncio
    async def test_send_message_system_sudo_appends_dangerous_skip_permissions(self, tmp_path, claude_config):
        remote_payload = {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({"result": "remote-result", "usage": {"input_tokens": 1, "output_tokens": 1}}),
            "stderr": "",
        }
        remote = FakeRemoteClient(remote_payload)
        agent = ClaudeCodeAgent(
            "claude",
            claude_config,
            tmp_path,
            runtime_mode="system",
            instance_id="ops-a",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test prompt", run_as_root=True):
                chunks.append(chunk)

        assert "remote-result" in "".join(chunks)
        assert not mock_exec.called
        action = remote.calls[0]["action"]
        assert "--dangerously-skip-permissions" in action["args"]
        assert "--permission-mode" in action["args"]
        idx = action["args"].index("--permission-mode")
        assert action["args"][idx + 1] == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_send_message_system_without_sudo_keeps_default_permissions(self, tmp_path, claude_config):
        remote_payload = {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({"result": "remote-result", "usage": {"input_tokens": 1, "output_tokens": 1}}),
            "stderr": "",
        }
        remote = FakeRemoteClient(remote_payload)
        agent = ClaudeCodeAgent(
            "claude",
            claude_config,
            tmp_path,
            runtime_mode="system",
            instance_id="ops-a",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test prompt", run_as_root=False):
                chunks.append(chunk)

        assert "remote-result" in "".join(chunks)
        assert not mock_exec.called
        action = remote.calls[0]["action"]
        assert "--dangerously-skip-permissions" not in action["args"]

    @pytest.mark.asyncio
    async def test_send_message_timeout(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
            text = "".join(chunks)
            assert "超时" in text

    @pytest.mark.asyncio
    async def test_send_message_nonzero_exit(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = _make_mock_process(stdout_data=b"error output", stderr_data=b"err", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
            text = "".join(chunks)
            assert "Exit code: 1" in text

    @pytest.mark.asyncio
    async def test_send_message_invalid_json(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = _make_mock_process(stdout_data=b"not json at all", returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
            assert "not json at all" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_send_message_command_not_found(self, agent):
        session = await agent.create_session("u1", "c1")
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude not found")):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
            text = "".join(chunks)
            assert "未安装" in text or "未找到" in text

    @pytest.mark.asyncio
    async def test_send_message_session_not_found(self, agent):
        with pytest.raises(ValueError, match="not found"):
            async for _ in agent.send_message("nonexistent", "test"):
                pass


class TestCancel:

    @pytest.mark.asyncio
    async def test_cancel(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()
        agent._processes[session.session_id] = mock_proc

        await agent.cancel(session.session_id)
        mock_proc.kill.assert_called_once()
        assert session.session_id not in agent._processes


class TestDestroySession:

    @pytest.mark.asyncio
    async def test_destroy_session(self, agent):
        session = await agent.create_session("u1", "c1")
        sid = session.session_id
        await agent.destroy_session(sid)
        assert sid not in agent.sessions
        assert sid not in agent._initialized_sessions

    @pytest.mark.asyncio
    async def test_destroy_nonexistent(self, agent):
        with pytest.raises(ValueError):
            await agent.destroy_session("nonexistent")


class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_check_active(self, agent):
        session = await agent.create_session("u1", "c1")
        h = agent.health_check(session.session_id)
        assert h["alive"] is True
        assert h["busy"] is False

    def test_health_check_missing(self, agent):
        h = agent.health_check("missing")
        assert h["alive"] is False


class TestProcessLifecycle:

    @pytest.mark.asyncio
    async def test_is_process_alive_false_when_no_process(self, agent):
        assert agent.is_process_alive("s1") is False

    @pytest.mark.asyncio
    async def test_is_process_alive_true(self, agent):
        mock_proc = MagicMock()
        mock_proc.returncode = None  # Still running
        agent._processes["s1"] = mock_proc
        assert agent.is_process_alive("s1") is True

    @pytest.mark.asyncio
    async def test_is_process_alive_false_when_finished(self, agent):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        agent._processes["s1"] = mock_proc
        assert agent.is_process_alive("s1") is False
