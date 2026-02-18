"""Integration tests for interactive /sysauth setup enrollment flow."""

from __future__ import annotations

import json
import time

import pytest

from channels.base import IncomingMessage
from core.auth import Auth
from core.router import Router
from core.two_factor import TwoFactorManager


def _system_config(sample_config: dict) -> dict:
    cfg = dict(sample_config)
    cfg["runtime"] = {"mode": "system", "instance_id": "ops-a"}
    cfg["two_factor"] = {"issuer": "CLI Gateway"}
    return cfg


@pytest.mark.asyncio
async def test_sysauth_setup_start_and_verify_persists_secret(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
    monkeypatch,
):
    import core.commands.sysauth_cmd as sysauth_cmd

    async def _fake_send_qr_file(ctx, otpauth_uri: str) -> bool:
        assert otpauth_uri.startswith("otpauth://totp/")
        return True

    monkeypatch.setattr(sysauth_cmd, "_send_qr_file", _fake_send_qr_file)

    state_file = tmp_path / "two_factor_state.json"
    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    two_factor = TwoFactorManager(enabled=True, state_file=str(state_file))
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=two_factor,
    )

    await router.handle_message(
        IncomingMessage(
            channel="telegram",
            chat_id="chat_1",
            user_id="123",
            text="/sysauth setup start",
            is_private=True,
            is_reply_to_bot=False,
            is_mention_bot=False,
        )
    )
    start_text = fake_channel.last_sent_text() or ""
    assert "已创建 2FA 绑定会话" in start_text
    assert "setup verify" in start_text

    pending_secret = two_factor._pending_enrollments["123"].secret  # type: ignore[attr-defined]
    code = two_factor._totp_code(pending_secret, time.time())
    await router.handle_message(
        IncomingMessage(
            channel="telegram",
            chat_id="chat_1",
            user_id="123",
            text=f"/sysauth setup verify {code}",
            is_private=True,
            is_reply_to_bot=False,
            is_mention_bot=False,
        )
    )
    verify_text = fake_channel.last_sent_text() or ""
    assert "绑定成功" in verify_text
    assert two_factor.secrets_by_user["123"] == pending_secret

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["secrets"]["123"] == pending_secret


@pytest.mark.asyncio
async def test_sysauth_setup_start_fallback_contains_otpauth_uri(
    session_manager,
    mock_agent,
    fake_channel,
    sample_config,
    billing,
    tmp_path,
    monkeypatch,
):
    import core.commands.sysauth_cmd as sysauth_cmd

    async def _fake_send_qr_file(ctx, otpauth_uri: str) -> bool:
        return False

    monkeypatch.setattr(sysauth_cmd, "_send_qr_file", _fake_send_qr_file)

    auth = Auth(
        channel_allowed={"telegram": ["123"]},
        state_file=str(tmp_path / "auth.json"),
        system_admin_users=["123"],
    )
    two_factor = TwoFactorManager(enabled=True)
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=two_factor,
    )

    await router.handle_message(
        IncomingMessage(
            channel="telegram",
            chat_id="chat_1",
            user_id="123",
            text="/sysauth setup start",
            is_private=True,
            is_reply_to_bot=False,
            is_mention_bot=False,
        )
    )
    text = fake_channel.last_sent_text() or ""
    assert "otpauth" in text
    assert "二维码发送失败" in text


@pytest.mark.asyncio
async def test_sysauth_approve_usage_keeps_html_escaped_placeholders(
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
    two_factor = TwoFactorManager(enabled=True)
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=_system_config(sample_config),
        billing=billing,
        two_factor=two_factor,
    )

    await router.handle_message(
        IncomingMessage(
            channel="telegram",
            chat_id="chat_1",
            user_id="123",
            text="/sysauth approve only_challenge_id",
            is_private=True,
            is_reply_to_bot=False,
            is_mention_bot=False,
        )
    )
    text = fake_channel.last_sent_text() or ""
    assert "用法: /sysauth approve" in text
    assert "&lt;challenge_id&gt;" in text
    assert "&lt;totp_code&gt;" in text
