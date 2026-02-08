"""
Channel rules loader - injects channel-specific context into agent sessions.

Each channel has a rules/*.md file that describes the interaction context
(formatting rules, tone, limitations) so the AI understands how it's being accessed.
"""
import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Default rules directory relative to project root
DEFAULT_RULES_DIR = Path(__file__).parent.parent / "rules"


class RulesLoader:
    """Load and cache channel-specific rules for session injection."""

    def __init__(self, rules_dir: Optional[Path] = None):
        self.rules_dir = rules_dir or DEFAULT_RULES_DIR
        self._cache: Dict[str, str] = {}
        logger.info(f"RulesLoader initialized with rules_dir={self.rules_dir}")

    def get_rules(self, channel_name: str) -> Optional[str]:
        """
        Get the rules content for a given channel.

        Args:
            channel_name: Channel identifier (e.g., "telegram", "discord", "email")

        Returns:
            Rules markdown content, or None if not found.
        """
        if channel_name in self._cache:
            return self._cache[channel_name]

        rules_file = self.rules_dir / f"{channel_name}.md"
        if not rules_file.exists():
            logger.warning(f"No rules file found for channel '{channel_name}': {rules_file}")
            return None

        try:
            content = rules_file.read_text(encoding="utf-8").strip()
            self._cache[channel_name] = content
            logger.info(f"Loaded rules for channel '{channel_name}' ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"Failed to load rules for '{channel_name}': {e}")
            return None

    def get_system_prompt(self, channel_name: str) -> str:
        """
        Build the system prompt prefix for a session based on channel.

        This should be prepended to the first message or injected as
        a system context when creating a new agent session.

        Args:
            channel_name: Channel identifier

        Returns:
            System prompt string (may be empty if no rules found).
        """
        rules = self.get_rules(channel_name)
        if not rules:
            return ""

        return (
            f"[CHANNEL CONTEXT]\n"
            f"{rules}\n"
            f"[END CHANNEL CONTEXT]\n\n"
        )

    def reload(self, channel_name: Optional[str] = None):
        """
        Clear cache and reload rules.

        Args:
            channel_name: If specified, only reload this channel. Otherwise reload all.
        """
        if channel_name:
            self._cache.pop(channel_name, None)
        else:
            self._cache.clear()
        logger.info(f"Rules cache cleared for: {channel_name or 'all'}")
