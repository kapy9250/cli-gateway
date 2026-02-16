"""Tests for core/formatter.py — OutputFormatter module."""

import pytest

from core.formatter import OutputFormatter


@pytest.fixture
def html_formatter():
    return OutputFormatter({"max_message_length": 100, "parse_mode": "HTML"})


@pytest.fixture
def md_formatter():
    return OutputFormatter({"max_message_length": 100, "parse_mode": "Markdown"})


class TestClean:

    def test_clean_ansi_codes(self, html_formatter):
        text = "\x1b[31mRed text\x1b[0m Normal"
        assert html_formatter.clean(text) == "Red text Normal"

    def test_clean_carriage_returns(self, html_formatter):
        text = "line1\r\nline2\rline3"
        result = html_formatter.clean(text)
        assert "\r" not in result
        assert "line1\nline2\nline3" == result

    def test_clean_excessive_blank_lines(self, html_formatter):
        text = "a\n\n\n\n\nb"
        assert html_formatter.clean(text) == "a\n\nb"

    def test_clean_strips_whitespace(self, html_formatter):
        assert html_formatter.clean("  hello  ") == "hello"


class TestFormatCodeBlock:

    def test_format_code_block_html(self, html_formatter):
        result = html_formatter.format_code_block("print('hi')", "python")
        assert '<pre><code class="language-python">' in result
        assert "print(&#39;hi&#39;)" in result

    def test_format_code_block_html_no_language(self, html_formatter):
        result = html_formatter.format_code_block("code")
        assert "<pre><code>code</code></pre>" == result

    def test_format_code_block_markdown(self, md_formatter):
        result = md_formatter.format_code_block("code", "python")
        assert result == "```python\ncode\n```"


class TestHtmlEscape:

    def test_html_escape(self, html_formatter):
        assert html_formatter._html_escape('<b>"test" & \'foo\'</b>') == (
            "&lt;b&gt;&quot;test&quot; &amp; &#39;foo&#39;&lt;/b&gt;"
        )


class TestSplitMessage:

    def test_split_short_message(self, html_formatter):
        chunks = html_formatter.split_message("short")
        assert chunks == ["short"]

    def test_split_at_newline(self):
        f = OutputFormatter({"max_message_length": 20})
        text = "abcdefghij\nabcdef\nrest"
        chunks = f.split_message(text)
        assert len(chunks) >= 2
        # All chunks should be <= 20 chars (excluding marker)

    def test_split_at_space(self):
        f = OutputFormatter({"max_message_length": 20})
        text = "word " * 10  # 50 chars
        chunks = f.split_message(text)
        assert len(chunks) >= 2

    def test_split_hard(self):
        f = OutputFormatter({"max_message_length": 10})
        text = "a" * 25  # No spaces or newlines
        chunks = f.split_message(text)
        assert len(chunks) >= 2

    def test_split_markers(self):
        f = OutputFormatter({"max_message_length": 30})
        text = "a" * 80
        chunks = f.split_message(text)
        assert len(chunks) >= 2
        # First chunk should have [1/N] marker
        assert "[1/" in chunks[0]

    def test_split_multiple_chunks(self):
        f = OutputFormatter({"max_message_length": 30})
        text = "word " * 50  # 250 chars
        chunks = f.split_message(text)
        assert len(chunks) >= 3
