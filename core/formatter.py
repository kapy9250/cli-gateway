"""
Output formatting and cleaning for chat-friendly display
"""
import re
import logging
from typing import List

logger = logging.getLogger(__name__)


class OutputFormatter:
    """Clean and format CLI output for messaging platforms"""
    
    def __init__(self, config: dict):
        """
        Initialize formatter
        
        Args:
            config: Channel configuration (for max_message_length, parse_mode, etc.)
        """
        self.config = config
        self.max_length = config.get("max_message_length", 4096)
        self.parse_mode = config.get("parse_mode", "HTML")
    
    def clean(self, text: str) -> str:
        """
        Clean CLI output:
        1. Strip ANSI escape codes
        2. Remove progress bars/spinners
        3. Basic formatting cleanup
        
        Args:
            text: Raw CLI output
            
        Returns:
            Cleaned text
        """
        # Strip ANSI escape codes (colors, cursor control, etc.)
        text = self._strip_ansi(text)
        
        # Remove carriage returns (used for progress bars)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Remove excessive blank lines (more than 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()
    
    def format_code_block(self, code: str, language: str = "") -> str:
        """
        Format code block based on parse_mode
        
        Args:
            code: Code content
            language: Language hint (e.g., "python", "bash")
            
        Returns:
            Formatted code block
        """
        if self.parse_mode == "HTML":
            if language:
                return f'<pre><code class="language-{language}">{self._html_escape(code)}</code></pre>'
            else:
                return f'<pre><code>{self._html_escape(code)}</code></pre>'
        else:  # Markdown
            return f'```{language}\n{code}\n```'
    
    def split_message(self, text: str) -> List[str]:
        """
        Split long text into multiple messages
        
        Strategy:
        - Prefer splitting at newlines
        - Avoid splitting inside code blocks
        - Add continuation markers
        
        Args:
            text: Text to split
            
        Returns:
            List of message chunks
        """
        if len(text) <= self.max_length:
            return [text]
        
        chunks = []
        remaining = text
        part_num = 1
        
        while remaining:
            if len(remaining) <= self.max_length:
                # Last chunk
                chunks.append(remaining)
                break
            
            # Find split point (prefer newline near max_length)
            split_at = self._find_split_point(remaining, self.max_length)
            
            chunk = remaining[:split_at].rstrip()
            remaining = remaining[split_at:].lstrip()
            
            # Add marker if not last chunk
            if remaining:
                chunk += f"\n\n[{part_num}/...]"
                part_num += 1
            
            chunks.append(chunk)
        
        # Update markers with total count
        total = len(chunks)
        if total > 1:
            for i in range(len(chunks)):
                chunks[i] = re.sub(r'\[(\d+)/\.\.\.\]', f'[{i+1}/{total}]', chunks[i])
        
        return chunks
    
    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes"""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)
    
    def _html_escape(self, text: str) -> str:
        """Escape HTML special characters"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))
    
    def _find_split_point(self, text: str, max_pos: int) -> int:
        """
        Find optimal split point before max_pos
        
        Prefers (in order):
        1. Last newline in last 20% of chunk
        2. Last space in last 20% of chunk
        3. max_pos exactly
        """
        search_start = int(max_pos * 0.8)
        
        # Look for newline
        newline_pos = text.rfind('\n', search_start, max_pos)
        if newline_pos > 0:
            return newline_pos + 1
        
        # Look for space
        space_pos = text.rfind(' ', search_start, max_pos)
        if space_pos > 0:
            return space_pos + 1
        
        # Hard split
        return max_pos
