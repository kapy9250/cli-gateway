"""Auth middleware — checks whitelist + rate limit."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


async def auth_middleware(ctx: "Context", next: Callable[[], Awaitable[None]]) -> None:
    allowed, reason = ctx.auth.check_detailed(ctx.user_id, channel=ctx.channel_name)
    if not allowed:
        if reason == "rate_limited":
            await ctx.router._reply(ctx.message, "⚠️ 请求过于频繁，请稍后再试")
        else:
            logger.warning("Unauthorized access: user_id=%s channel=%s", ctx.user_id, ctx.channel_name)
            await ctx.router._reply(ctx.message, "⚠️ 未授权访问")
        return
    await next()
