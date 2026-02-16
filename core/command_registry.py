"""Declarative command registration with @command decorator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)

# Handler signature: async def handler(ctx: Context) -> None
CommandHandler = Callable[["Context"], Awaitable[None]]


@dataclass
class CommandSpec:
    """Metadata for a registered gateway command."""

    name: str  # e.g. "/help"
    description: str  # e.g. "显示帮助"
    handler: CommandHandler


class CommandRegistry:
    """Central store of gateway commands."""

    def __init__(self) -> None:
        self._commands: Dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        if spec.name in self._commands:
            logger.warning("Command %s registered twice, overwriting", spec.name)
        self._commands[spec.name] = spec

    def get(self, name: str) -> Optional[CommandSpec]:
        return self._commands.get(name)

    def list_all(self) -> List[CommandSpec]:
        return sorted(self._commands.values(), key=lambda s: s.name)


# ── Module-level singleton used by the @command decorator ──
registry = CommandRegistry()


def command(name: str, description: str = ""):
    """Decorator that registers an async handler as a gateway command.

    Usage::

        @command("/help", "显示帮助")
        async def handle_help(ctx: Context) -> None:
            ...
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        registry.register(CommandSpec(name=name, description=description, handler=func))
        return func

    return decorator
