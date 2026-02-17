"""System-auth commands for 2FA challenge and approval flow."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


def _usage() -> str:
    return "\n".join(
        [
            "用法:",
            "• /sysauth plan <action text>",
            "• /sysauth approve <challenge_id> <totp_code>",
            "• /sysauth status <challenge_id>",
        ]
    )


@command("/sysauth", "系统级 2FA 审批")
async def handle_sysauth(ctx: "Context") -> None:
    manager = ctx.two_factor
    if manager is None:
        await ctx.router._reply(ctx.message, "❌ Two-factor manager not available")
        return
    if not bool(getattr(manager, "enabled", False)):
        await ctx.router._reply(ctx.message, "❌ two_factor.enabled=false，/sysauth 已禁用")
        return

    text = (ctx.message.text or "").strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        await ctx.router._reply(ctx.message, _usage())
        return

    sub = parts[1].lower()
    if sub == "plan":
        if len(parts) < 3 or not parts[2].strip():
            await ctx.router._reply(ctx.message, "用法: /sysauth plan <action text>")
            return
        action_text = parts[2].strip()
        challenge = manager.create_challenge(
            ctx.user_id,
            {
                "action": action_text,
                "channel": ctx.message.channel,
                "chat_id": ctx.message.chat_id,
                "user_id": ctx.user_id,
            },
        )
        ttl = int(challenge.expires_at - challenge.created_at)
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "✅ 已创建 2FA 审批请求",
                    f"- challenge_id: <code>{challenge.challenge_id}</code>",
                    f"- ttl_seconds: <code>{ttl}</code>",
                    f"- action_hash: <code>{challenge.action_hash[:16]}...</code>",
                    "下一步: /sysauth approve <challenge_id> <totp_code>",
                ]
            ),
        )
        return

    if sub == "approve":
        # Re-split because code shouldn't be swallowed by maxsplit=2 format.
        items = text.split()
        if len(items) < 4:
            await ctx.router._reply(ctx.message, "用法: /sysauth approve <challenge_id> <totp_code>")
            return
        challenge_id = items[2].strip()
        code = items[3].strip()
        ok, reason = manager.approve_challenge(challenge_id, ctx.user_id, code)
        if not ok:
            await ctx.router._reply(ctx.message, f"❌ 2FA 审批失败: <code>{reason}</code>")
            return
        await ctx.router._reply(ctx.message, "✅ 2FA 审批通过")
        return

    if sub == "status":
        items = text.split()
        if len(items) < 3:
            await ctx.router._reply(ctx.message, "用法: /sysauth status <challenge_id>")
            return
        challenge_id = items[2].strip()
        st = manager.status(challenge_id, ctx.user_id)
        if not st.get("exists"):
            await ctx.router._reply(ctx.message, "❌ challenge 不存在或不属于你")
            return
        now = time.time()
        expires_in = int(float(st.get("expires_at", now)) - now)
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "ℹ️ 2FA challenge 状态",
                    f"- challenge_id: <code>{st.get('challenge_id')}</code>",
                    f"- approved: <code>{str(bool(st.get('approved'))).lower()}</code>",
                    f"- expires_in: <code>{expires_in}</code>",
                ]
            ),
        )
        return

    await ctx.router._reply(ctx.message, _usage())
