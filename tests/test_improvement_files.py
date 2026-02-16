"""Tests for Improvement 1: File retrieval (/files, /download)."""

import pytest
from pathlib import Path

from channels.base import IncomingMessage


class TestFilesCommand:
    """The /files command lists files in the active session's ai/ directory."""

    @pytest.mark.asyncio
    async def test_files_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/files"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_files_empty_workspace(self, router, make_message, fake_channel, mock_agent):
        # Create session first
        await router.handle_message(make_message(text="hello"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/files"))
        text = fake_channel.last_sent_text()
        assert "暂无" in text or "空" in text or "没有" in text

    @pytest.mark.asyncio
    async def test_files_lists_files(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        # Place files in the ai/ output directory
        sid = mock_agent.created_sessions[0]
        ai_dir = mock_agent.sessions[sid].work_dir / "ai"
        (ai_dir / "result.py").write_text("print('hello')")
        (ai_dir / "report.md").write_text("# Report")

        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/files"))
        text = fake_channel.last_sent_text()
        assert "result.py" in text
        assert "report.md" in text


class TestDownloadCommand:
    """The /download <filename> command sends a file from the ai/ directory."""

    @pytest.mark.asyncio
    async def test_download_no_session(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/download test.py"))
        text = fake_channel.last_sent_text()
        assert "无活跃" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_download_no_arg(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/download"))
        text = fake_channel.last_sent_text()
        assert "用法" in text or "filename" in text.lower()

    @pytest.mark.asyncio
    async def test_download_file_not_found(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/download nonexistent.txt"))
        text = fake_channel.last_sent_text()
        assert "未找到" in text or "不存在" in text or "❌" in text

    @pytest.mark.asyncio
    async def test_download_success(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        sid = mock_agent.created_sessions[0]
        ai_dir = mock_agent.sessions[sid].work_dir / "ai"
        (ai_dir / "output.py").write_text("code here")

        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/download output.py"))
        # Should have called send_file
        assert len(fake_channel.files_sent) >= 1
        _, filepath, _ = fake_channel.files_sent[-1]
        assert "output.py" in filepath

    @pytest.mark.asyncio
    async def test_download_path_traversal_blocked(self, router, make_message, fake_channel, mock_agent):
        await router.handle_message(make_message(text="hello"))
        fake_channel.sent.clear()
        await router.handle_message(make_message(text="/download ../../../etc/passwd"))
        text = fake_channel.last_sent_text()
        assert "❌" in text or "非法" in text or "未找到" in text
