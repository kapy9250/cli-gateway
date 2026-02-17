"""Tests for short-lived system action grants."""

from __future__ import annotations

from core.system_grant import SystemGrantManager


def test_issue_and_verify_success():
    manager = SystemGrantManager(secret="test-secret", ttl_seconds=60)
    action = {"op": "docker_exec", "args": ["ps"], "user_id": "u1"}
    token = manager.issue("u1", action, now=1000)
    ok, reason, grant = manager.verify(token, "u1", action, now=1001)
    assert ok is True
    assert reason == "ok"
    assert grant is not None
    assert grant.user_id == "u1"


def test_verify_rejects_replay():
    manager = SystemGrantManager(secret="test-secret", ttl_seconds=60)
    action = {"op": "config_delete", "path": "/etc/app.conf", "user_id": "u1"}
    token = manager.issue("u1", action, now=1000)

    ok1, reason1, _ = manager.verify(token, "u1", action, now=1001)
    ok2, reason2, _ = manager.verify(token, "u1", action, now=1002)
    assert ok1 is True
    assert reason1 == "ok"
    assert ok2 is False
    assert reason2 == "token_replayed"


def test_verify_rejects_action_mismatch():
    manager = SystemGrantManager(secret="test-secret", ttl_seconds=60)
    good_action = {"op": "cron_delete", "name": "job-a", "user_id": "u1"}
    bad_action = {"op": "cron_delete", "name": "job-b", "user_id": "u1"}
    token = manager.issue("u1", good_action, now=1000)
    ok, reason, _ = manager.verify(token, "u1", bad_action, now=1001)
    assert ok is False
    assert reason == "token_action_mismatch"


def test_verify_rejects_expired():
    manager = SystemGrantManager(secret="test-secret", ttl_seconds=10)
    action = {"op": "read_file", "path": "/etc/hosts", "max_bytes": 100, "user_id": "u1"}
    token = manager.issue("u1", action, now=1000)
    ok, reason, _ = manager.verify(token, "u1", action, now=1010)
    assert ok is False
    assert reason == "token_expired"


def test_verify_rejects_signature_tamper():
    manager = SystemGrantManager(secret="test-secret", ttl_seconds=60)
    action = {"op": "docker_exec", "args": ["ps"], "user_id": "u1"}
    token = manager.issue("u1", action, now=1000)
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    ok, reason, _ = manager.verify(bad, "u1", action, now=1001)
    assert ok is False
    assert reason == "token_signature_invalid"
