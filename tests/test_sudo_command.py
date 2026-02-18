"""Integration tests for /sudo command and 2FA flow."""

from __future__ import annotations

import time

import pytest

from channels.base import IncomingMessage
from core.auth import Auth
from core.router import Router
from core.sudo_state import SudoStateManager
from core.two_factor import TwoFactorManager


class _FakeSystemClient:
    async def execute(self, user_id: str, action: dict, grant_token: str | None = None):
        return {"ok": True}


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


def _build_router(
    *,
    mode: str,
    auth: Auth,
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    two_factor: TwoFactorManager,
    system_client,
):
    return Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_config(sample_config, mode),
        billing=billing,
        two_factor=two_factor,
        sudo_state=SudoStateManager(ttl_seconds=600),
        system_client=system_client,
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
    router = _build_router(
        mode="system",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=two_factor,
        system_client=_FakeSystemClient(),
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
    router = _build_router(
        mode="system",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=two_factor,
        system_client=_FakeSystemClient(),
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
    router = _build_router(
        mode="session",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_client=_FakeSystemClient(),
    )

    await router.handle_message(_msg("/sudo on"))
    text = fake_channel.last_sent_text() or ""
    assert "user 模式" in text


@pytest.mark.asyncio
async def test_sudo_fail_closed_without_system_client(
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
    router = _build_router(
        mode="system",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_client=None,
    )

    await router.handle_message(_msg("/sudo on"))
    text = fake_channel.last_sent_text() or ""
    assert "fail-closed" in text


@pytest.mark.asyncio
async def test_sudo_usage_status_and_off_when_disabled(
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
    router = _build_router(
        mode="system",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_client=_FakeSystemClient(),
    )

    await router.handle_message(_msg("/sudo"))
    assert "用法:" in (fake_channel.last_sent_text() or "")

    await router.handle_message(_msg("/sudo status"))
    assert "off" in (fake_channel.last_sent_text() or "")

    await router.handle_message(_msg("/sudo off"))
    assert "已是关闭状态" in (fake_channel.last_sent_text() or "")


@pytest.mark.asyncio
async def test_sudo_rejects_non_admin_and_requires_2fa_enabled(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
):
    auth_non_admin = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth1.json"),
        system_admin_users=["999"],
    )
    router_non_admin = _build_router(
        mode="system",
        auth=auth_non_admin,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=TwoFactorManager(enabled=True, secrets_by_user={"123": "JBSWY3DPEHPK3PXP"}),
        system_client=_FakeSystemClient(),
    )
    await router_non_admin.handle_message(_msg("/sudo on", user_id="123"))
    assert "仅 system_admin" in (fake_channel.last_sent_text() or "")

    auth_admin = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth2.json"),
        system_admin_users=["123"],
    )
    router_no_2fa = _build_router(
        mode="system",
        auth=auth_admin,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=TwoFactorManager(enabled=False),
        system_client=_FakeSystemClient(),
    )
    await router_no_2fa.handle_message(_msg("/sudo on"))
    assert "two_factor.enabled=false" in (fake_channel.last_sent_text() or "")


@pytest.mark.asyncio
async def test_sudo_challenge_arg_validation_and_explicit_challenge_path(
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
    router = _build_router(
        mode="system",
        auth=auth,
        session_manager=session_manager,
        mock_agent=mock_agent,
        fake_channel=fake_channel,
        sample_config=sample_config,
        billing=billing,
        two_factor=two_factor,
        system_client=_FakeSystemClient(),
    )

    await router.handle_message(_msg("/sudo on --challenge"))
    assert "需要 challenge_id" in (fake_channel.last_sent_text() or "")

    payload = {"op": "sudo_on", "scope": {"channel": "telegram", "chat_id": "chat_1"}}
    challenge = two_factor.create_challenge("123", payload)
    code = two_factor._totp_code(secret, time.time())
    ok, _ = two_factor.approve_challenge(challenge.challenge_id, "123", code, payload)
    assert ok

    await router.handle_message(_msg(f"/sudo on --challenge {challenge.challenge_id}"))
    text = fake_channel.last_sent_text() or ""
    assert "已开启" in text
