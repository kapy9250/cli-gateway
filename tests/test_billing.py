"""Tests for core/billing.py — BillingTracker module."""

import json
from pathlib import Path

import pytest

from core.billing import BillingTracker


class TestRecord:

    def test_record_basic(self, billing):
        entry = billing.record(
            session_id="s1", user_id="u1", channel="telegram",
            agent_name="claude", model="sonnet",
            input_tokens=100, output_tokens=50, cost_usd=0.001,
        )
        assert entry["session_id"] == "s1"
        assert entry["cost_usd"] == 0.001
        assert entry["cumulative_cost_usd"] == 0.001

    def test_cumulative_cost(self, billing):
        billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.01)
        entry = billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.02)
        assert abs(entry["cumulative_cost_usd"] - 0.03) < 1e-6

    def test_record_fields(self, billing):
        entry = billing.record(
            session_id="s1", user_id="u1", channel="telegram",
            agent_name="claude", model="opus",
            input_tokens=100, output_tokens=50,
            cache_read_tokens=10, cache_creation_tokens=5,
            cost_usd=0.005, duration_ms=1234,
        )
        expected_keys = {
            "timestamp", "session_id", "user_id", "channel", "agent", "model",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
            "cost_usd", "cumulative_cost_usd", "duration_ms",
        }
        assert set(entry.keys()) == expected_keys


class TestGetSessionTotal:

    def test_get_session_total(self, billing):
        billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.01)
        billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.02)
        assert abs(billing.get_session_total("s1") - 0.03) < 1e-6

    def test_get_session_total_unknown(self, billing):
        assert billing.get_session_total("unknown") == 0.0


class TestMultipleSessions:

    def test_multiple_sessions_isolated(self, billing):
        billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.01)
        billing.record(session_id="s2", user_id="u2", channel="t", agent_name="c", cost_usd=0.05)
        assert abs(billing.get_session_total("s1") - 0.01) < 1e-6
        assert abs(billing.get_session_total("s2") - 0.05) < 1e-6


class TestPersistence:

    def test_persistence_reload(self, tmp_path):
        billing_dir = str(tmp_path / "billing")
        b1 = BillingTracker(billing_dir=billing_dir)
        b1.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.01)
        b1.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.02)

        # New instance should reload cumulative
        b2 = BillingTracker(billing_dir=billing_dir)
        assert abs(b2.get_session_total("s1") - 0.03) < 1e-6


class TestConcurrency:

    def test_concurrent_records(self, billing):
        """Thread-safety: record from multiple threads."""
        import threading

        errors = []

        def record_many():
            try:
                for _ in range(50):
                    billing.record(session_id="s1", user_id="u1", channel="t", agent_name="c", cost_usd=0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert abs(billing.get_session_total("s1") - 0.2) < 1e-4
