"""Tests for utils/helpers.py — utility functions."""

import os
import pytest

from utils.helpers import load_config, sanitize_session_id, truncate_text


class TestLoadConfig:

    def test_load_config_basic(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: value\nnested:\n  a: 1\n", encoding="utf-8")
        result = load_config(str(cfg))
        assert result["key"] == "value"
        assert result["nested"]["a"] == 1

    def test_load_config_env_substitution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_TOKEN", "secret123")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("token: ${TEST_TOKEN}\n", encoding="utf-8")
        result = load_config(str(cfg))
        assert result["token"] == "secret123"

    def test_load_config_missing_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MISSING_VAR_XYZ", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text("token: ${MISSING_VAR_XYZ}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="MISSING_VAR_XYZ"):
            load_config(str(cfg))

    def test_load_config_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")


class TestSanitizeSessionId:

    def test_sanitize_valid(self):
        assert sanitize_session_id("abcd1234") == "abcd1234"

    def test_sanitize_invalid_too_short(self):
        with pytest.raises(ValueError):
            sanitize_session_id("abc")

    def test_sanitize_invalid_chars(self):
        with pytest.raises(ValueError):
            sanitize_session_id("abcd123g")

    def test_sanitize_path_traversal(self):
        with pytest.raises(ValueError):
            sanitize_session_id("../../../")


class TestTruncateText:

    def test_truncate_short(self):
        assert truncate_text("hello", 100) == "hello"

    def test_truncate_long(self):
        text = "word " * 50
        result = truncate_text(text, 30)
        assert len(result) <= 30 + 3  # +3 for "..."
        assert result.endswith("...")

    def test_truncate_with_html(self):
        text = "<b>bold</b> and <i>italic</i> " * 20
        result = truncate_text(text, 50)
        assert "<b>" not in result  # HTML stripped before truncation
        assert len(result) <= 53

    def test_truncate_with_markdown(self):
        text = "**bold** and _italic_ " * 20
        result = truncate_text(text, 50)
        assert "**" not in result
