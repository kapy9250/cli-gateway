"""Integration-ish tests for /sys command with remote system client."""

from __future__ import annotations

import time

import pytest

from channels.base import IncomingMessage
from core.auth import Auth
from core.router import Router
from core.system_grant import SystemGrantManager
from core.two_factor import TwoFactorManager


class FakeSystemClient:
    def __init__(self, response: dict):
        self.response = dict(response)
        self.calls = []

    async def execute(self, user_id: str, action: dict, grant_token: str = None):
        self.calls.append(
            {
                "user_id": str(user_id),
                "action": dict(action or {}),
                "grant_token": grant_token,
            }
        )
        return dict(self.response)


def _system_config(sample_config: dict) -> dict:
    cfg = dict(sample_config)
    cfg["runtime"] = {"mode": "system"}
    return cfg


@pytest.mark.asyncio
async def test_sys_journal_requires_2fa_challenge_before_remote_call(
    session_manager, mock_agent, fake_channel, sample_config, billing, tmp_path
):
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    remote = FakeSystemClient({"ok": True, "lines": 5, "output": "remote-journal"})
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_executor=None,
        system_client=remote,
        system_grant=SystemGrantManager(secret="bridge-secret", ttl_seconds=60),
    )
    msg = IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id="123",
        text="kapy sys journal 5",
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )
    await router.handle_message(msg)

    assert not remote.calls
    text = fake_channel.last_sent_text() or ""
    assert "该操作需要 2FA 审批" in text
    # Telegram HTML parse mode would eat raw <totp_code>; keep placeholder escaped.
    assert "&lt;totp_code&gt;" in text


@pytest.mark.asyncio
async def test_sys_journal_with_approved_challenge_sends_grant_token(
    session_manager, mock_agent, fake_channel, sample_config, billing, tmp_path
):
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    secret = "JBSWY3DPEHPK3PXP"
    two_factor = TwoFactorManager(enabled=True, secrets_by_user={"123": secret})
    action = {"op": "journal", "unit": None, "lines": 5}
    challenge = two_factor.create_challenge("123", action)
    code = two_factor._totp_code(secret, time.time())
    ok, _ = two_factor.approve_challenge(challenge.challenge_id, "123", code, action)
    assert ok is True

    remote = FakeSystemClient({"ok": True, "lines": 5, "output": "remote-journal"})
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=two_factor,
        system_executor=None,
        system_client=remote,
        system_grant=SystemGrantManager(secret="bridge-secret", ttl_seconds=60),
    )
    msg = IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id="123",
        text=f"/sys journal 5 --challenge {challenge.challenge_id}",
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )
    await router.handle_message(msg)

    assert len(remote.calls) == 1
    call = remote.calls[0]
    assert call["action"] == action
    assert isinstance(call["grant_token"], str) and call["grant_token"]
    assert "journal 输出" in (fake_channel.last_sent_text() or "")


@pytest.mark.asyncio
async def test_sys_docker_with_approved_challenge_sends_grant_token(
    session_manager, mock_agent, fake_channel, sample_config, billing, tmp_path
):
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    secret = "JBSWY3DPEHPK3PXP"
    two_factor = TwoFactorManager(enabled=True, secrets_by_user={"123": secret})
    action = {"op": "docker_exec", "args": ["ps"]}
    challenge = two_factor.create_challenge("123", action)
    code = two_factor._totp_code(secret, time.time())
    ok, _ = two_factor.approve_challenge(challenge.challenge_id, "123", code, action)
    assert ok is True

    remote = FakeSystemClient({"ok": True, "returncode": 0, "truncated": False, "output": "docker-ok"})
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=two_factor,
        system_executor=None,
        system_client=remote,
        system_grant=SystemGrantManager(secret="bridge-secret", ttl_seconds=60),
    )
    msg = IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id="123",
        text=f"/sys docker ps --challenge {challenge.challenge_id}",
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )
    await router.handle_message(msg)

    assert len(remote.calls) == 1
    call = remote.calls[0]
    assert call["action"] == action
    assert isinstance(call["grant_token"], str) and call["grant_token"]
    assert "docker 执行成功" in (fake_channel.last_sent_text() or "")


@pytest.mark.asyncio
async def test_sys_docker_rejects_when_two_factor_disabled(
    session_manager, mock_agent, fake_channel, sample_config, billing, tmp_path
):
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    remote = FakeSystemClient({"ok": True, "returncode": 0, "truncated": False, "output": "docker-ok"})
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=TwoFactorManager(enabled=False),
        system_executor=None,
        system_client=remote,
        system_grant=SystemGrantManager(secret="bridge-secret", ttl_seconds=60),
    )
    msg = IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id="123",
        text="/sys docker ps",
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )
    await router.handle_message(msg)

    assert not remote.calls
    assert "two_factor.enabled=false" in (fake_channel.last_sent_text() or "")


@pytest.mark.asyncio
async def test_sys_rejects_when_remote_bridge_unavailable(
    session_manager, mock_agent, fake_channel, sample_config, billing, tmp_path
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
        config=_system_config(sample_config),
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_executor=object(),
        system_client=None,
        system_grant=SystemGrantManager(secret="bridge-secret", ttl_seconds=60),
    )
    msg = IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id="123",
        text="/sys journal 5",
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )
    await router.handle_message(msg)
    assert "remote bridge 未配置" in (fake_channel.last_sent_text() or "")
