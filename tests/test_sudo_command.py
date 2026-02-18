"""Integration tests for /sudo command and 2FA flow."""

from __future__ import annotations

import time

import pytest

from channels.base import IncomingMessage
from core.auth import Auth
from core.router import Router
from core.sudo_state import SudoStateManager
from core.two_factor import TwoFactorManager


def _config(sample_config: dict, mode: str) -> dict:
    cfg = dict(sample_config)
    cfg["runtime"] = {"mode": mode, "instance_id": "ops-a"}
    return cfg


def _msg(text: str, user_id: str = "123") -> IncomingMessage:
    return IncomingMessage(
        channel="telegram",
        chat_id="chat_1",
        user_id=user_id,
        text=text,
        is_private=True,
        is_reply_to_bot=False,
        is_mention_bot=False,
    )


@pytest.mark.asyncio
async def test_sudo_on_and_off_flow(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
):
    secret = "JBSWY3DPEHPK3PXP"
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    two_factor = TwoFactorManager(enabled=True, secrets_by_user={"123": secret})
    sudo_state = SudoStateManager(ttl_seconds=600)
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_config(sample_config, "system"),
        billing=billing,
        two_factor=two_factor,
        sudo_state=sudo_state,
    )

    await router.handle_message(_msg("/sudo on"))
    first = fake_channel.last_sent_text() or ""
    assert "sudo on 需要 2FA" in first
    pending = two_factor.get_pending_approval_input("123")
    assert pending is not None

    code = two_factor._totp_code(secret, time.time())
    await router.handle_message(_msg(code))
    second = fake_channel.last_sent_text() or ""
    assert "sudo 已开启" in second
    assert "sudo: <code>on</code>" in second
    st = router.get_sudo_status("123", "telegram", "chat_1")
    assert st["enabled"] is True

    await router.handle_message(_msg("/sudo off"))
    third = fake_channel.last_sent_text() or ""
    assert "sudo 已关闭" in third
    st2 = router.get_sudo_status("123", "telegram", "chat_1")
    assert st2["enabled"] is False


@pytest.mark.asyncio
async def test_sudo_rejects_non_code_reply(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
):
    secret = "JBSWY3DPEHPK3PXP"
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    two_factor = TwoFactorManager(enabled=True, secrets_by_user={"123": secret})
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_config(sample_config, "system"),
        billing=billing,
        two_factor=two_factor,
        sudo_state=SudoStateManager(ttl_seconds=600),
    )

    await router.handle_message(_msg("/sudo on"))
    await router.handle_message(_msg("not-a-code"))
    text = fake_channel.last_sent_text() or ""
    assert "2FA 验证失败" in text
    assert two_factor.get_pending_approval_input("123") is None


@pytest.mark.asyncio
async def test_sudo_blocked_in_user_mode(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
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
        config=_config(sample_config, "session"),
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        sudo_state=SudoStateManager(ttl_seconds=600),
    )

    await router.handle_message(_msg("/sudo on"))
    text = fake_channel.last_sent_text() or ""
    assert "user 模式" in text
