"""Tests for core/rules.py — RulesLoader module."""

import pytest
from pathlib import Path

from core.rules import RulesLoader


@pytest.fixture
def rules_dir(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "telegram.md").write_text("You are in a Telegram chat.", encoding="utf-8")
    (d / "discord.md").write_text("You are in Discord.", encoding="utf-8")
    return d


@pytest.fixture
def loader(rules_dir):
    return RulesLoader(rules_dir=rules_dir)


class TestGetRules:

    def test_get_rules_existing(self, loader):
        rules = loader.get_rules("telegram")
        assert rules == "You are in a Telegram chat."

    def test_get_rules_missing(self, loader):
        assert loader.get_rules("sms") is None

    def test_get_rules_cached(self, loader, rules_dir):
        loader.get_rules("telegram")
        # Modify file after cache
        (rules_dir / "telegram.md").write_text("MODIFIED", encoding="utf-8")
        # Should still return cached version
        assert loader.get_rules("telegram") == "You are in a Telegram chat."


class TestGetSystemPrompt:

    def test_get_system_prompt(self, loader):
        prompt = loader.get_system_prompt("telegram")
        assert "[CHANNEL CONTEXT]" in prompt
        assert "You are in a Telegram chat." in prompt
        assert "[END CHANNEL CONTEXT]" in prompt

    def test_get_system_prompt_no_rules(self, loader):
        assert loader.get_system_prompt("sms") == ""


class TestReload:

    def test_reload_specific(self, loader):
        loader.get_rules("telegram")  # cache
        loader.reload("telegram")
        assert "telegram" not in loader._cache

    def test_reload_all(self, loader):
        loader.get_rules("telegram")
        loader.get_rules("discord")
        loader.reload()
        assert len(loader._cache) == 0
