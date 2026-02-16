"""Tests for channels/base.py — Base channel and data classes."""

import pytest

from channels.base import Attachment, BaseChannel, IncomingMessage


class TestIncomingMessage:

    def test_defaults(self):
        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="u1",
            text="hello", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
        )
        assert msg.channel == "telegram"
        assert msg.text == "hello"
        assert msg.attachments is None or msg.attachments == []
        assert msg.session_hint is None
        assert msg.reply_to_text is None

    def test_with_attachments(self):
        att = Attachment(filename="f.txt", filepath="/tmp/f.txt", mime_type="text/plain", size_bytes=100)
        msg = IncomingMessage(
            channel="telegram", chat_id="c1", user_id="u1",
            text="see file", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
            attachments=[att],
        )
        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "f.txt"

    def test_session_hint(self):
        msg = IncomingMessage(
            channel="email", chat_id="c1", user_id="u1",
            text="continue", is_private=True, is_reply_to_bot=False, is_mention_bot=False,
            session_hint="abc123",
        )
        assert msg.session_hint == "abc123"


class TestAttachment:

    def test_attachment_fields(self):
        att = Attachment(filename="img.png", filepath="/tmp/img.png", mime_type="image/png", size_bytes=1024)
        assert att.filename == "img.png"
        assert att.mime_type == "image/png"
        assert att.size_bytes == 1024


class TestBaseChannelAbstract:

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseChannel({"max_message_length": 4096})
