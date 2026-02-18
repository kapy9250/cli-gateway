"""Tests for utils/runtime_version.py."""

from __future__ import annotations

from pathlib import Path

from utils import runtime_version


def test_detect_runtime_version_prefers_env(monkeypatch):
    runtime_version.detect_runtime_version.cache_clear()
    monkeypatch.setenv("CLI_GATEWAY_VERSION", "build-20260218")
    assert runtime_version.detect_runtime_version() == "build-20260218"


def test_detect_runtime_version_fallback_unknown(monkeypatch):
    runtime_version.detect_runtime_version.cache_clear()
    monkeypatch.delenv("CLI_GATEWAY_VERSION", raising=False)
    monkeypatch.delenv("CLI_GATEWAY_VERSION_FILE", raising=False)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(runtime_version.subprocess, "check_output", _raise)
    monkeypatch.setattr(runtime_version, "_version_file_path", lambda: Path("/nonexistent/version-file"))
    assert runtime_version.detect_runtime_version() == "unknown"


def test_detect_runtime_version_prefers_version_file(monkeypatch, tmp_path):
    runtime_version.detect_runtime_version.cache_clear()
    monkeypatch.delenv("CLI_GATEWAY_VERSION", raising=False)
    version_file = tmp_path / ".runtime-version"
    version_file.write_text("deploy:abc123\n", encoding="utf-8")
    monkeypatch.setenv("CLI_GATEWAY_VERSION_FILE", str(version_file))
    assert runtime_version.detect_runtime_version() == "deploy:abc123"
