"""Auth middleware — checks whitelist + rate limit."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TYPE_CHECKING

from utils.runtime_mode import is_system_mode

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


def _is_system_mode(ctx: "Context") -> bool:
    runtime = ((ctx.config or {}).get("runtime") or {})
    return is_system_mode(runtime.get("mode", "session"))


async def _enforce_system_admin_if_needed(ctx: "Context") -> bool:
    if not _is_system_mode(ctx):
        return True
    if ctx.auth.is_system_admin(ctx.user_id):
        return True
    logger.warning(
        "Blocked non-system-admin in system mode: user_id=%s channel=%s",
        ctx.user_id,
        ctx.channel_name,
    )
    await ctx.router._reply(ctx.message, "⚠️ 当前实例为 system 模式，仅 system_admin 可访问")
    return False


async def auth_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    if ctx.channel_name == "discord":
        discord_cfg = (ctx.config or {}).get("channels", {}).get("discord", {})
        allow_bots = discord_cfg.get("allow_bots", discord_cfg.get("allowBots", True))
        allowed_guilds = {str(g) for g in discord_cfg.get("allowed_guilds", [])}

        if ctx.message.is_private:
            # Discord DM: only user allowlist is honored
            allowed, reason = ctx.auth.check_detailed(ctx.user_id, channel=ctx.channel_name)
            if not allowed:
                if reason == "rate_limited":
                    await ctx.router._reply(ctx.message, "⚠️ 请求过于频繁，请稍后再试")
                else:
                    logger.warning("Unauthorized Discord DM user_id=%s", ctx.user_id)
                    await ctx.router._reply(ctx.message, "⚠️ 未授权访问")
                return
            if not await _enforce_system_admin_if_needed(ctx):
                return
            await call_next()
            return

        # Discord guild: must be in guild whitelist
        guild_id = str(ctx.message.guild_id) if ctx.message.guild_id else ""
        if guild_id not in allowed_guilds:
            logger.warning("Unauthorized Discord guild_id=%s user_id=%s", guild_id, ctx.user_id)
            return

        # If bot messages are disabled, reject bot-authored guild messages.
        if getattr(ctx.message, "is_from_bot", False) and not allow_bots:
            return

        if not await _enforce_system_admin_if_needed(ctx):
            return
        await call_next()
        return

    allowed, reason = ctx.auth.check_detailed(ctx.user_id, channel=ctx.channel_name)
    if not allowed:
        if reason == "rate_limited":
            await ctx.router._reply(ctx.message, "⚠️ 请求过于频繁，请稍后再试")
        else:
            logger.warning("Unauthorized access: user_id=%s channel=%s", ctx.user_id, ctx.channel_name)
            await ctx.router._reply(ctx.message, "⚠️ 未授权访问")
        return
    if not await _enforce_system_admin_if_needed(ctx):
        return
    await call_next()
