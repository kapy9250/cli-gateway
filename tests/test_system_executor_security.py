"""Security tests for SystemExecutor path handling."""

from __future__ import annotations

from pathlib import Path

from core.system_executor import SystemExecutor


def test_write_path_rejects_dotdot_escape(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    executor = SystemExecutor(
        {
            "enabled": True,
            "write_allowed_paths": [str(allowed)],
        }
    )
    escaped = outside / "x.txt"
    dotdot_path = allowed / ".." / "outside" / "x.txt"
    assert str(dotdot_path) != str(escaped)
    assert executor.is_write_allowed(str(dotdot_path)) is False


def test_write_file_rejects_symlink_escape(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("original", encoding="utf-8")

    link = allowed / "secret-link.txt"
    link.symlink_to(target)

    executor = SystemExecutor(
        {
            "enabled": True,
            "write_allowed_paths": [str(allowed)],
        }
    )
    result = executor.write_file(str(link), "changed", append=False)
    assert result.get("ok") is False
    assert result.get("reason") == "write_path_not_allowed"
    assert target.read_text(encoding="utf-8") == "original"


def test_sensitive_path_matches_normalized_target(tmp_path):
    sensitive = tmp_path / "sensitive" / "token.txt"
    sensitive.parent.mkdir()
    sensitive.write_text("secret", encoding="utf-8")

    executor = SystemExecutor(
        {
            "enabled": True,
            "sensitive_read_paths": [str(sensitive)],
        }
    )
    alias = sensitive.parent / ".." / "sensitive" / "token.txt"
    assert executor.is_sensitive_path(str(alias)) is True


def test_read_file_clamps_requested_limit(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("x" * 300, encoding="utf-8")

    executor = SystemExecutor(
        {
            "enabled": True,
            "max_read_bytes": 10,
        }
    )
    high = executor.read_file(str(target), max_bytes=200)
    negative = executor.read_file(str(target), max_bytes=-1)
    zero = executor.read_file(str(target), max_bytes=0)

    assert high.get("ok") is True
    assert high.get("returned_bytes") == 10
    assert negative.get("returned_bytes") == 10
    assert zero.get("returned_bytes") == 10


def test_read_file_does_not_use_path_read_bytes(tmp_path, monkeypatch):
    target = tmp_path / "sample.txt"
    target.write_text("hello world", encoding="utf-8")

    def _boom(_self):
        raise AssertionError("Path.read_bytes should not be used in read_file")

    monkeypatch.setattr(Path, "read_bytes", _boom)

    executor = SystemExecutor(
        {
            "enabled": True,
            "max_read_bytes": 5,
        }
    )
    result = executor.read_file(str(target), max_bytes=5)
    assert result.get("ok") is True
    assert result.get("returned_bytes") == 5
