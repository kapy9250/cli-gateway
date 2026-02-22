"""Tests for agents/gemini_cli.py — GeminiAgent."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agents.gemini_cli import GeminiAgent


class FakeRemoteClient:
    def __init__(self, response: dict):
        self.response = dict(response)
        self.calls = []

    async def execute(self, user_id: str, action: dict, grant_token: str = None):
        self.calls.append({"user_id": str(user_id), "action": dict(action or {})})
        return dict(self.response)


@pytest.fixture
def gemini_config():
    return {
        "command": "gemini",
        "args_template": ["-p", "{prompt}", "--session-id", "{session_id}"],
        "models": {"gemini3": "gemini-3.0"},
        "default_model": "gemini3",
        "supported_params": {"model": "--model", "temperature": "--temperature"},
        "default_params": {},
        "timeout": 5,
    }


@pytest.fixture
def agent(tmp_path, gemini_config):
    return GeminiAgent("gemini", gemini_config, tmp_path)


def _make_streaming_process(lines: list[bytes], returncode: int = 0):
    proc = AsyncMock()
    proc.returncode = returncode
    line_iter = iter(lines + [b""])
    async def readline():
        return next(line_iter)
    proc.stdout = AsyncMock()
    proc.stdout.readline = readline
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.wait = AsyncMock()
    proc.kill = AsyncMock()
    return proc


class TestCreateSession:

    @pytest.mark.asyncio
    async def test_create_session(self, agent):
        info = await agent.create_session("u1", "c1")
        assert info.agent_name == "gemini"
        assert info.work_dir.exists()
        assert info.session_id in agent.sessions


class TestSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_streaming(self, agent):
        session = await agent.create_session("u1", "c1")
        mock_proc = _make_streaming_process([b"hello\n", b"world\n"], returncode=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
        assert len(chunks) >= 2

    @pytest.mark.asyncio
    async def test_send_message_command_not_found(self, agent):
        session = await agent.create_session("u1", "c1")
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test"):
                chunks.append(chunk)
            assert any("未安装" in c or "未找到" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_send_message_system_sudo_sets_yolo_and_disables_sandbox(self, tmp_path, gemini_config):
        cfg = dict(gemini_config)
        cfg["args_template"] = ["-p", "{prompt}", "--approval-mode", "default", "--sandbox=true"]
        remote = FakeRemoteClient({"ok": True, "returncode": 0, "stdout": "remote-ok", "stderr": ""})
        agent = GeminiAgent(
            "gemini",
            cfg,
            tmp_path,
            runtime_mode="system",
            instance_id="ops-a",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test", run_as_root=True):
                chunks.append(chunk)

        assert "remote-ok" in "".join(chunks)
        assert not mock_exec.called
        action = remote.calls[0]["action"]
        assert "--yolo" in action["args"]
        assert "--approval-mode" in action["args"]
        idx = action["args"].index("--approval-mode")
        assert action["args"][idx + 1] == "yolo"
        assert "--sandbox=false" in action["args"]
        assert "--sandbox=true" not in action["args"]

    @pytest.mark.asyncio
    async def test_send_message_system_without_sudo_keeps_default_approval_mode(self, tmp_path, gemini_config):
        cfg = dict(gemini_config)
        cfg["args_template"] = ["-p", "{prompt}", "--approval-mode", "default", "--sandbox=true"]
        remote = FakeRemoteClient({"ok": True, "returncode": 0, "stdout": "remote-ok", "stderr": ""})
        agent = GeminiAgent(
            "gemini",
            cfg,
            tmp_path,
            runtime_mode="system",
            instance_id="ops-a",
            system_client=remote,
        )
        session = await agent.create_session("u1", "c1")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            chunks = []
            async for chunk in agent.send_message(session.session_id, "test", run_as_root=False):
                chunks.append(chunk)

        assert "remote-ok" in "".join(chunks)
        assert not mock_exec.called
        action = remote.calls[0]["action"]
        assert "--approval-mode" in action["args"]
        idx = action["args"].index("--approval-mode")
        assert action["args"][idx + 1] == "default"
        assert "--yolo" not in action["args"]
        assert "--sandbox=true" in action["args"]
        assert "--sandbox=false" not in action["args"]


class TestDestroySession:

    @pytest.mark.asyncio
    async def test_destroy_session(self, agent):
        session = await agent.create_session("u1", "c1")
        await agent.destroy_session(session.session_id)
        assert session.session_id not in agent.sessions


class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_check(self, agent):
        session = await agent.create_session("u1", "c1")
        h = agent.health_check(session.session_id)
        assert h["alive"] is True
        assert h["busy"] is False
