"""Tests for utils/runtime_version.py."""

from __future__ import annotations

from utils import runtime_version


def test_detect_runtime_version_prefers_env(monkeypatch):
    runtime_version.detect_runtime_version.cache_clear()
    monkeypatch.setenv("CLI_GATEWAY_VERSION", "build-20260218")
    assert runtime_version.detect_runtime_version() == "build-20260218"


def test_detect_runtime_version_fallback_unknown(monkeypatch):
    runtime_version.detect_runtime_version.cache_clear()
    monkeypatch.delenv("CLI_GATEWAY_VERSION", raising=False)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(runtime_version.subprocess, "check_output", _raise)
    assert runtime_version.detect_runtime_version() == "unknown"
