"""Command parser middleware — dispatches gateway commands via the registry."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Awaitable, Callable, TYPE_CHECKING

from core.command_registry import registry

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


async def command_parser_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    text = (ctx.message.text or "").strip()

    # ── Support "kapy <subcommand>" shorthand ──
    if text.lower().startswith("kapy "):
        subcommand = text[5:].strip()
        if subcommand:
            # Rewrite message with "/" prefix
            ctx.message = replace(ctx.message, text=f"/{subcommand}")
            text = ctx.message.text
        else:
            await ctx.router._reply(ctx.message, "用法: kapy &lt;command&gt; [args]\n发送 'kapy help' 查看帮助")
            return

    if not text.startswith("/"):
        await call_next()
        return

    parts = text.split()
    cmd_name = parts[0].split("@")[0].lower()

    spec = registry.get(cmd_name)
    if spec:
        await spec.handler(ctx)
    else:
        # Not a known gateway command — forward to agent
        logger.info("Forwarding command %s to agent", cmd_name)
        await call_next()
