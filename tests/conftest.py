"""Global fixtures for CLI Gateway test suite."""

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.base import BaseAgent, SessionInfo, UsageInfo
from channels.base import Attachment, BaseChannel, IncomingMessage
from core.auth import Auth
from core.billing import BillingTracker
from core.session import SessionManager


# ── FakeChannel ──


class FakeChannel(BaseChannel):
    """Test double that records all interactions."""

    def __init__(self):
        super().__init__({"max_message_length": 4096, "parse_mode": "HTML"})
        self.sent: List[Tuple[str, str]] = []  # (chat_id, text)
        self.edited: List[Tuple[str, int, str]] = []  # (chat_id, msg_id, text)
        self.files_sent: List[Tuple[str, str, str]] = []  # (chat_id, filepath, caption)
        self.typing_count: int = 0
        self.supports_streaming: bool = True
        self._next_message_id: int = 100

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_text(self, chat_id: str, text: str) -> int:
        self.sent.append((chat_id, text))
        mid = self._next_message_id
        self._next_message_id += 1
        return mid

    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        self.files_sent.append((chat_id, filepath, caption))

    async def send_typing(self, chat_id: str):
        self.typing_count += 1

    async def edit_message(self, chat_id: str, message_id: int, text: str):
        self.edited.append((chat_id, message_id, text))

    def last_sent_text(self) -> Optional[str]:
        return self.sent[-1][1] if self.sent else None

    async def cleanup_attachments(self, message: IncomingMessage):
        pass


# ── MockAgent ──


class MockAgent(BaseAgent):
    """Test double for BaseAgent."""

    def __init__(self, name: str = "claude", config: dict = None, workspace_base: Path = None):
        cfg = config or {
            "command": "claude",
            "args_template": ["-p", "{prompt}", "--session-id", "{session_id}"],
            "models": {"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-6", "haiku": "claude-haiku-4-5"},
            "default_model": "sonnet",
            "supported_params": {"model": "--model", "thinking": "--thinking", "max_turns": "--max-turns"},
            "default_params": {"thinking": "low"},
        }
        ws = workspace_base or Path("/tmp/test-mock-agent")
        ws.mkdir(parents=True, exist_ok=True)
        super().__init__(name, cfg, ws)
        self.created_sessions: List[str] = []
        self.destroyed_sessions: List[str] = []
        self.messages_received: List[Tuple[str, str]] = []  # (session_id, message)
        self._response_chunks: List[str] = ["Hello from mock agent!"]
        self._usage = UsageInfo(input_tokens=100, output_tokens=50, cost_usd=0.001, model="test-model")

    def set_response(self, chunks: List[str]):
        self._response_chunks = chunks

    async def create_session(
        self,
        user_id: str,
        chat_id: str,
        session_id: str = None,
        work_dir: Path = None,
        scope_dir: str = None,
    ) -> SessionInfo:
        sid = str(session_id or f"mock-{len(self.created_sessions):04d}")
        existing = self.sessions.get(sid)
        if existing is not None:
            existing.last_active = time.time()
            return existing

        if work_dir is None:
            base_dir = self.workspace_base / str(scope_dir) if scope_dir else self.workspace_base
            work_dir = base_dir / f"sess_{sid}"
        else:
            work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        self.init_workspace(work_dir)
        session = SessionInfo(
            session_id=sid,
            agent_name=self.name,
            user_id=str(user_id),
            work_dir=work_dir,
            created_at=time.time(),
            last_active=time.time(),
        )
        self.sessions[sid] = session
        self.created_sessions.append(sid)
        return session

    async def send_message(
        self,
        session_id: str,
        message: str,
        model: str = None,
        params: dict = None,
        run_as_root: bool = False,
    ) -> AsyncIterator[str]:
        self.messages_received.append((session_id, message))
        session = self.sessions.get(session_id)
        if session:
            session.is_busy = True
        for chunk in self._response_chunks:
            yield chunk
        if session:
            session.is_busy = False
        self._last_usage[session_id] = UsageInfo(
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
            cost_usd=self._usage.cost_usd,
            model=self._usage.model,
        )

    async def cancel(self, session_id: str):
        session = self.sessions.get(session_id)
        if session:
            session.is_busy = False

    async def destroy_session(self, session_id: str):
        self.sessions.pop(session_id, None)
        self.destroyed_sessions.append(session_id)

    def health_check(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        return {
            "alive": session is not None,
            "pid": None,
            "memory_mb": 0,
            "busy": session.is_busy if session else False,
            "pending_seconds": None,
        }


# ── Fixtures ──


@pytest.fixture
def tmp_workspace(tmp_path):
    """Temporary workspace directory."""
    ws = tmp_path / "workspaces"
    ws.mkdir()
    return ws


@pytest.fixture
def sample_config():
    """Standard test configuration dict."""
    return {
        "default_agent": "claude",
        "agents": {
            "claude": {
                "enabled": True,
                "command": "claude",
                "args_template": ["-p", "{prompt}", "--session-id", "{session_id}", "--output-format", "text"],
                "models": {"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-6", "haiku": "claude-haiku-4-5"},
                "default_model": "sonnet",
                "supported_params": {"model": "--model", "thinking": "--thinking", "max_turns": "--max-turns"},
                "default_params": {"thinking": "low"},
                "timeout": 300,
            },
            "codex": {
                "enabled": True,
                "command": "codex",
                "args_template": ["-p", "{prompt}"],
                "models": {"gpt5": "gpt-5.3"},
                "default_model": "gpt5",
                "supported_params": {"model": "--model", "temperature": "--temperature"},
                "default_params": {},
            },
        },
        "channels": {
            "telegram": {"enabled": True, "allowed_users": ["123"], "parse_mode": "HTML", "max_message_length": 4096},
        },
        "session": {"workspace_base": "/tmp/test-ws", "max_sessions_per_user": 5},
        "billing": {"dir": "/tmp/test-billing"},
        "logging": {"level": "WARNING"},
    }


@pytest.fixture
def auth(tmp_path):
    """Auth instance with telegram user '123' allowed."""
    state = tmp_path / "auth_state.json"
    return Auth(
        channel_allowed={"telegram": ["123"], "discord": ["456"]},
        max_requests_per_minute=0,
        state_file=str(state),
        admin_users=["123"],
    )


@pytest.fixture
def session_manager(tmp_workspace):
    """SessionManager backed by tmp workspace."""
    return SessionManager(workspace_base=tmp_workspace, max_sessions_per_user=5, cleanup_inactive_after_hours=24)


@pytest.fixture
def mock_agent(tmp_path):
    """MockAgent instance."""
    return MockAgent(workspace_base=tmp_path / "agent_ws")


@pytest.fixture
def mock_codex_agent(tmp_path):
    """MockAgent with codex config."""
    return MockAgent(
        name="codex",
        config={
            "command": "codex",
            "args_template": ["-p", "{prompt}"],
            "models": {"gpt5": "gpt-5.3"},
            "default_model": "gpt5",
            "supported_params": {"model": "--model", "temperature": "--temperature"},
            "default_params": {},
        },
        workspace_base=tmp_path / "codex_ws",
    )


@pytest.fixture
def fake_channel():
    """FakeChannel instance."""
    return FakeChannel()


@pytest.fixture
def billing(tmp_path):
    """BillingTracker backed by tmp dir."""
    return BillingTracker(billing_dir=str(tmp_path / "billing"))


@pytest.fixture
def make_message():
    """Factory to create IncomingMessage easily."""
    def _make(
        text: str = "hello",
        user_id: str = "123",
        chat_id: str = "chat_1",
        channel: str = "telegram",
        is_private: bool = True,
        is_from_bot: bool = False,
        guild_id: str = None,
        sender_username: str = None,
        sender_display_name: str = None,
        sender_mention: str = None,
        attachments: list = None,
        session_hint: str = None,
    ) -> IncomingMessage:
        return IncomingMessage(
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            is_private=is_private,
            is_reply_to_bot=False,
            is_mention_bot=False,
            is_from_bot=is_from_bot,
            guild_id=guild_id,
            sender_username=sender_username,
            sender_display_name=sender_display_name,
            sender_mention=sender_mention,
            attachments=attachments or [],
            session_hint=session_hint,
        )
    return _make


@pytest.fixture
def router(auth, session_manager, mock_agent, fake_channel, sample_config, billing):
    """Router wired to mock components."""
    from core.router import Router
    return Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=sample_config,
        billing=billing,
    )


@pytest.fixture
def multi_agent_router(auth, session_manager, mock_agent, mock_codex_agent, fake_channel, sample_config, billing):
    """Router with multiple agents."""
    from core.router import Router
    return Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent, "codex": mock_codex_agent},
        channel=fake_channel,
        config=sample_config,
        billing=billing,
    )
