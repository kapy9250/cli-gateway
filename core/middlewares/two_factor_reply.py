"""Interactive 2FA reply middleware.

When a system command creates a pending 2FA challenge, the next user message
must be a 6-digit TOTP code. If it is not a code, verification fails closed.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Awaitable, Callable, TYPE_CHECKING

from utils.runtime_mode import is_system_mode

if TYPE_CHECKING:
    from core.pipeline import Context


_TOTP_CODE_RE = re.compile(r"^\d{6}$")


async def two_factor_reply_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    manager = ctx.two_factor
    if manager is None or not bool(getattr(manager, "enabled", False)):
        await call_next()
        return

    runtime_mode = ((ctx.config or {}).get("runtime") or {}).get("mode", "session")
    if not is_system_mode(runtime_mode):
        await call_next()
        return

    pending = manager.get_pending_approval_input(ctx.user_id)
    if not pending:
        await call_next()
        return

    text = str(ctx.message.text or "").strip()
    if _TOTP_CODE_RE.fullmatch(text):
        ok, reason, approved = manager.approve_pending_input_code(ctx.user_id, text)
        if not ok or not approved:
            await ctx.router._reply(ctx.message, f"❌ 2FA 验证失败: <code>{reason}</code>")
            return

        manager.activate_approval_window(
            ctx.user_id,
            ctx.message.channel,
            ctx.message.chat_id,
        )

        retry_cmd = str(approved.get("retry_cmd", "")).strip()
        challenge_id = str(approved.get("challenge_id", "")).strip()
        if not retry_cmd:
            await ctx.router._reply(ctx.message, "❌ 2FA 验证失败: <code>retry_command_missing</code>")
            return
        if "--challenge" not in retry_cmd and challenge_id:
            retry_cmd = f"{retry_cmd} --challenge {challenge_id}"

        # Rewrite this message into the stored command (e.g. /sudo on), then continue.
        ctx.message = replace(ctx.message, text=retry_cmd)
        await call_next()
        return

    manager.clear_pending_approval_input(ctx.user_id, revoke_challenge=True)
    await ctx.router._reply(
        ctx.message,
        "❌ 2FA 验证失败：本次只接受 6 位验证码输入。验证已结束，请重新发起系统操作。",
    )
