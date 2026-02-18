"""Tests for agents/codex_cli.py — CodexAgent."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.codex_cli import CodexAgent


class FakeRemoteClient:
    def __init__(self, response: dict):
        self.response = dict(response)
        self.calls = []

    async def execute(self, user_id: str, action: dict, grant_token: str = None):
        self.calls.append({"user_id": str(user_id), "action": dict(action or {})})
        return dict(self.response)


@pytest.fixture
def codex_config():
    return {
        "command": "codex",
        "args_template": ["-p", "{prompt}", "--session-id", "{session_id}"],
        "models": {"gpt5": "gpt-5.3"},
        "default_model": "gpt5",
        "supported_params": {"model": "--model", "temperature": "--temperature"},
        "default_params": {},
        "timeout": 5,
    }


@pytest.fixture
def agent(tmp_path, codex_config):
    return CodexAgent("codex", codex_config, tmp_path)


def _make_streaming_process(lines: list[bytes], returncode: int = 0):
    """Create a mock process that streams lines."""
    proc = AsyncMock()
    proc.returncode = returncode

    # stdout readline returns lines then b""
    line_iter = iter(lines + [b""])
    async def readline():
        return next(line_iter)
    proc.stdout = AsyncMock()
    proc.stdout.readline = readline

    # stderr
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")

    proc.wait = AsyncMock()
    proc.kill = AsyncMock()

    return proc


class TestCreateSession:

    @pytest.mark.asyncio
    async def test_create_session(self, agent):
        info = await agent.create_session("u1", "c1")
        assert info.session_id is not None
        assert info.agent_name == "codex"
        assert info.work_dir.exists()
        assert info.session_id in agent.sessions


class TestSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_streaming(self, agent):
        session = await agent.create_session("u1", "c1")
        lines = [b"line 1\n", b"line 2\n", b"line 3\n"]
        mock_proc = _make_streaming_process(lines, returncode=0)
        # Set returncode after wait
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)

        assert len(chunks) >= 3
        assert "line 1\n" in chunks

    @pytest.mark.asyncio
    async def test_send_message_adds_skip_git_repo_check(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = _make_streaming_process([b"ok\n"], returncode=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            async for _ in agent.send_message(session.session_id, "test"):
                pass

        args = mock_exec.call_args.args
        assert "--skip-git-repo-check" in args

    @pytest.mark.asyncio
    async def test_send_message_uses_remote_system_client_when_configured(self, tmp_path, codex_config):
        remote = FakeRemoteClient({"ok": True, "returncode": 0, "stdout": "remote-ok", "stderr": ""})
        agent = CodexAgent(
            "codex",
            codex_config,
            tmp_path,
            runtime_mode="session",
            instance_id="user-main",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)

        assert "remote-ok" in "".join(chunks)
        assert not mock_exec.called
        assert len(remote.calls) == 1
        action = remote.calls[0]["action"]
        assert action["op"] == "agent_cli_exec"
        assert action["agent"] == "codex"
        assert action["mode"] == "session"
        assert action["instance_id"] == "user-main"

    @pytest.mark.asyncio
    async def test_send_message_fails_closed_when_remote_required_but_unconfigured(self, tmp_path, codex_config):
        agent = CodexAgent(
            "codex",
            codex_config,
            tmp_path,
            runtime_mode="session",
            instance_id="user-main",
            system_client=None,
            remote_exec_required=True,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)

        text = "".join(chunks)
        assert "远程执行失败: system_client_required" in text
        assert not mock_exec.called

    @pytest.mark.asyncio
    async def test_send_message_command_not_found(self, agent):
        session = await agent.create_session("u1", "c1")
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
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

    @pytest.mark.asyncio
    async def test_busy_flag_reset_after_message(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = _make_streaming_process([b"ok\n"], returncode=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async for _ in agent.send_message(session.session_id, "test"):
                pass
        assert session.is_busy is False


class TestDestroySession:

    @pytest.mark.asyncio
    async def test_destroy_session(self, agent):
        session = await agent.create_session("u1", "c1")
        sid = session.session_id
        await agent.destroy_session(sid)
        assert sid not in agent.sessions

    @pytest.mark.asyncio
    async def test_destroy_nonexistent(self, agent):
        with pytest.raises(ValueError):
            await agent.destroy_session("nope")


class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_check_active(self, agent):
        session = await agent.create_session("u1", "c1")
        h = agent.health_check(session.session_id)
        assert h["alive"] is True

    def test_health_check_missing(self, agent):
        h = agent.health_check("missing")
        assert h["alive"] is False
