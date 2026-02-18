"""Tests for TwoFactorManager security behavior."""

from __future__ import annotations

import json
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


def test_enrollment_verify_persists_secret_state(tmp_path):
    state_file = tmp_path / "two_factor_state.json"
    manager = TwoFactorManager(enabled=True, state_file=str(state_file), issuer="CLI Gateway")

    enrollment = manager.begin_enrollment("u1", account_name="ops-a:u1")
    assert enrollment["secret"]
    assert enrollment["otpauth_uri"].startswith("otpauth://totp/")
    assert enrollment["already_configured"] is False

    code = manager._totp_code(enrollment["secret"], time.time())
    ok, reason = manager.verify_enrollment("u1", code)
    assert ok is True
    assert reason == "enrollment_verified"
    assert manager.secrets_by_user["u1"] == enrollment["secret"]

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["secrets"]["u1"] == enrollment["secret"]


def test_enrollment_status_and_cancel():
    manager = TwoFactorManager(enabled=True)
    st0 = manager.enrollment_status("u1")
    assert st0["configured"] is False
    assert st0["pending"] is False

    manager.begin_enrollment("u1", account_name="ops-a:u1")
    st1 = manager.enrollment_status("u1")
    assert st1["pending"] is True

    assert manager.cancel_enrollment("u1") is True
    st2 = manager.enrollment_status("u1")
    assert st2["pending"] is False


def test_state_file_loaded_on_startup(tmp_path):
    state_file = tmp_path / "two_factor_state.json"
    state_file.write_text(
        json.dumps({"version": 1, "secrets": {"u1": "STATESECRET123456"}}),
        encoding="utf-8",
    )
    manager = TwoFactorManager(enabled=True, state_file=str(state_file), secrets_by_user={"u1": "CONFIGSECRET999999"})
    assert manager.secrets_by_user["u1"] == "STATESECRET123456"


def test_pending_approval_input_lifecycle():
    secret = "JBSWY3DPEHPK3PXP"
    manager = TwoFactorManager(enabled=True, secrets_by_user={"u1": secret})
    action = {"op": "journal", "lines": 5}
    challenge = manager.create_challenge("u1", action)
    manager.set_pending_approval_input("u1", challenge.challenge_id, "/sys journal 5")

    pending = manager.get_pending_approval_input("u1")
    assert pending is not None
    assert pending["challenge_id"] == challenge.challenge_id

    code = manager._totp_code(secret, time.time())
    ok, reason, approved = manager.approve_pending_input_code("u1", code)
    assert ok is True
    assert reason == "approved"
    assert approved is not None
    assert approved["challenge_id"] == challenge.challenge_id
    assert manager.get_pending_approval_input("u1") is None
