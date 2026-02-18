"""Security tests for system/session mode authorization behavior."""

from __future__ import annotations

import pytest

from core.auth import Auth
from core.router import Router


def _base_config(mode: str) -> dict:
    return {
        "runtime": {"mode": mode},
        "default_agent": "claude",
        "agents": {
            "claude": {
                "enabled": True,
                "models": {"sonnet": "claude-sonnet-4-5"},
                "default_model": "sonnet",
                "supported_params": {"model": "--model"},
                "default_params": {},
            }
        },
        "channels": {
            "telegram": {
                "enabled": True,
                "allowed_users": ["123"],
                "parse_mode": "HTML",
                "max_message_length": 4096,
            },
            "discord": {
                "enabled": True,
                "token": "dummy",
                "allow_bots": True,
                "allowed_guilds": ["g1"],
            },
        },
        "session": {"workspace_base": "/tmp/test-ws", "max_sessions_per_user": 5},
        "billing": {"dir": "/tmp/test-billing"},
        "logging": {"level": "WARNING"},
    }


class TestSystemModeAuthGate:
    @pytest.mark.asyncio
    async def test_session_mode_blocks_sys_commands(
        self,
        auth,
        session_manager,
        mock_agent,
        fake_channel,
        billing,
        make_message,
    ):
        router = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=_base_config("session"),
            billing=billing,
        )

        await router.handle_message(make_message(text="kapy sys journal 10"))
        assert "/sys 指令已下线" in (fake_channel.last_sent_text() or "")
        assert not mock_agent.messages_received

    @pytest.mark.asyncio
    async def test_system_mode_blocks_non_system_admin_plain_text(
        self,
        tmp_path,
        session_manager,
        mock_agent,
        fake_channel,
        billing,
        make_message,
    ):
        auth = Auth(
            channel_allowed={"telegram": ["123"]},
            state_file=str(tmp_path / "auth.json"),
            system_admin_users=["999"],
        )
        router = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=_base_config("system"),
            billing=billing,
        )

        await router.handle_message(make_message(text="run uname -a", channel="telegram"))
        assert "仅 system_admin 可访问" in (fake_channel.last_sent_text() or "")
        assert not mock_agent.messages_received

    @pytest.mark.asyncio
    async def test_system_mode_allows_system_admin_plain_text(
        self,
        tmp_path,
        session_manager,
        mock_agent,
        fake_channel,
        billing,
        make_message,
    ):
        auth = Auth(
            channel_allowed={"telegram": ["123"]},
            state_file=str(tmp_path / "auth.json"),
            system_admin_users=["123"],
        )
        router = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=_base_config("system"),
            billing=billing,
        )

        await router.handle_message(make_message(text="hello", channel="telegram"))
        assert mock_agent.messages_received
        assert "Hello from mock agent!" in (fake_channel.last_sent_text() or "")

    @pytest.mark.asyncio
    async def test_system_mode_blocks_non_system_admin_in_discord_guild(
        self,
        tmp_path,
        session_manager,
        mock_agent,
        fake_channel,
        billing,
        make_message,
    ):
        auth = Auth(
            channel_allowed={"discord": ["123"]},
            state_file=str(tmp_path / "auth.json"),
            system_admin_users=["999"],
        )
        router = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=_base_config("system"),
            billing=billing,
        )

        await router.handle_message(
            make_message(
                text="hello",
                channel="discord",
                is_private=False,
                guild_id="g1",
                user_id="123",
            )
        )
        assert "仅 system_admin 可访问" in (fake_channel.last_sent_text() or "")
        assert not mock_agent.messages_received
