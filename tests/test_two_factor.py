"""Tests for TwoFactorManager security behavior."""

from __future__ import annotations

import time

from core.two_factor import TwoFactorManager


def test_approve_challenge_rejects_when_two_factor_disabled():
    manager = TwoFactorManager(enabled=False)
    action = {"op": "docker_exec", "args": ["ps"]}
    challenge = manager.create_challenge("u1", action)

    ok, reason = manager.approve_challenge(challenge.challenge_id, "u1", "000000", action)
    assert ok is False
    assert reason == "two_factor_disabled"

    ok2, reason2 = manager.consume_approval(challenge.challenge_id, "u1", action)
    assert ok2 is False
    assert reason2 == "challenge_not_approved"


def test_enabled_two_factor_requires_valid_totp_code():
    secret = "JBSWY3DPEHPK3PXP"
    manager = TwoFactorManager(enabled=True, secrets_by_user={"u1": secret})
    action = {"op": "docker_exec", "args": ["ps"]}
    challenge = manager.create_challenge("u1", action)

    bad_ok, bad_reason = manager.approve_challenge(challenge.challenge_id, "u1", "000000", action)
    assert bad_ok is False
    assert bad_reason == "totp_code_invalid"

    code = manager._totp_code(secret, time.time())
    ok, reason = manager.approve_challenge(challenge.challenge_id, "u1", code, action)
    assert ok is True
    assert reason == "approved"
