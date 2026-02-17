"""Tests for core/auth.py — Auth module."""

import json
import time
from pathlib import Path

import pytest

from core.auth import Auth


class TestAuthCheck:
    """Authorization check logic."""

    def test_check_allowed_user(self, auth):
        assert auth.check("123", channel="telegram") is True

    def test_check_disallowed_user(self, auth):
        assert auth.check("999", channel="telegram") is False

    def test_check_wrong_channel(self, auth):
        # User 123 is allowed on telegram, not discord
        assert auth.check("123", channel="discord") is False

    def test_check_on_correct_second_channel(self, auth):
        # User 456 is allowed on discord
        assert auth.check("456", channel="discord") is True

    def test_check_no_channel_fallback_allowed(self, auth):
        # channel=None → check any channel
        assert auth.check("123", channel=None) is True

    def test_check_no_channel_fallback_disallowed(self, auth):
        assert auth.check("999", channel=None) is False

    def test_check_unconfigured_channel(self, auth):
        # Channel "sms" has no allowlist → deny
        assert auth.check("123", channel="sms") is False

    def test_numeric_user_id_coerced_to_string(self):
        a = Auth(channel_allowed={"telegram": [123]})
        assert a.check("123", channel="telegram") is True


class TestRateLimiting:
    """Rate limiting behavior."""

    def test_rate_limiting_basic(self, tmp_path):
        a = Auth(channel_allowed={"telegram": ["1"]}, max_requests_per_minute=3)
        assert a.check("1", "telegram") is True
        assert a.check("1", "telegram") is True
        assert a.check("1", "telegram") is True
        assert a.check("1", "telegram") is False  # 4th request blocked

    def test_rate_limiting_window_expiry(self, tmp_path, monkeypatch):
        a = Auth(channel_allowed={"telegram": ["1"]}, max_requests_per_minute=2)
        # Fill the window
        a.check("1", "telegram")
        a.check("1", "telegram")
        assert a.check("1", "telegram") is False

        # Expire old entries by moving time forward
        old_deque = a._request_log["1"]
        # Manually age entries
        for i in range(len(old_deque)):
            old_deque[i] -= 61
        assert a.check("1", "telegram") is True

    def test_rate_limiting_disabled(self):
        a = Auth(channel_allowed={"telegram": ["1"]}, max_requests_per_minute=0)
        for _ in range(100):
            assert a.check("1", "telegram") is True


class TestUserMutation:
    """Adding and removing users."""

    def test_add_user(self, auth):
        auth.add_user("999", "telegram")
        assert auth.check("999", channel="telegram") is True

    def test_add_user_new_channel(self, auth):
        auth.add_user("777", "sms")
        assert auth.check("777", channel="sms") is True

    def test_remove_user(self, auth):
        auth.remove_user("123", "telegram")
        assert auth.check("123", channel="telegram") is False

    def test_remove_user_also_revokes_system_admin(self, auth):
        auth.add_system_admin("123")
        assert auth.is_system_admin("123") is True
        auth.remove_user("123", "telegram")
        assert auth.is_system_admin("123") is False

    def test_get_channel_users(self, auth):
        users = auth.get_channel_users("telegram")
        assert "123" in users

    def test_get_channel_users_empty(self, auth):
        users = auth.get_channel_users("nonexistent")
        assert users == set()


class TestAdminOperations:
    """Admin role management."""

    def test_is_admin(self, auth):
        assert auth.is_admin("123") is True
        assert auth.is_admin("999") is False

    def test_add_admin(self, auth):
        auth.add_admin("999")
        assert auth.is_admin("999") is True

    def test_remove_admin(self, auth):
        auth.remove_admin("123")
        assert auth.is_admin("123") is False


class TestSystemAdminOperations:
    """System-admin role management."""

    def test_is_system_admin(self, auth):
        assert auth.is_system_admin("123") is False

    def test_add_system_admin(self, auth):
        auth.add_system_admin("999")
        assert auth.is_system_admin("999") is True

    def test_remove_system_admin(self, auth):
        auth.add_system_admin("777")
        auth.remove_system_admin("777")
        assert auth.is_system_admin("777") is False


class TestAllowedUsersProperty:
    """The allowed_users property (union of all channels)."""

    def test_allowed_users_union(self, auth):
        users = auth.allowed_users
        assert "123" in users
        assert "456" in users


class TestPersistence:
    """State file persistence."""

    def test_state_persistence_save_load(self, tmp_path):
        state = tmp_path / "auth.json"
        a1 = Auth(channel_allowed={"telegram": ["1"]}, state_file=str(state), admin_users=["1"])
        a1.add_user("2", "discord")
        a1.add_admin("2")
        a1.add_system_admin("3")

        # Load fresh instance
        a2 = Auth(state_file=str(state))
        assert a2.check("1", "telegram") is True
        assert a2.check("2", "discord") is True
        assert a2.is_admin("2") is True
        assert a2.is_system_admin("3") is True

    def test_legacy_format_migration(self, tmp_path):
        state = tmp_path / "auth.json"
        # Write old format
        state.write_text(json.dumps({"allowed_users": ["111", "222"]}))
        a = Auth(state_file=str(state))
        assert a.check("111", "telegram") is True
        assert a.check("222", "telegram") is True
