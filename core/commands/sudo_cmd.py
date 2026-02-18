"""Sudo mode command for system gateway."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, List, Optional, Tuple

from core.command_registry import command
from utils.runtime_mode import is_system_mode

if TYPE_CHECKING:
    from core.pipeline import Context


def _usage() -> str:
    return "\n".join(
        [
            "用法:",
            "• /sudo status",
            "• /sudo on [--challenge <id>]",
            "• /sudo off",
            "",
            "说明:",
            "• 仅 sys 模式可用",
            "• /sudo on 会触发 2FA，直接回复 6 位验证码即可",
            "• 验证通过后 10 分钟内 Agent CLI 以 root 身份执行",
            "• 10 分钟后自动失效，或手动 /sudo off 立即关闭",
        ]
    )


def _extract_challenge(parts: List[str]) -> Tuple[List[str], Optional[str], Optional[str]]:
    out = []
    challenge_id = None
    i = 0
    while i < len(parts):
        token = parts[i]
        if token == "--challenge":
            if i + 1 >= len(parts):
                return [], None, "--challenge 需要 challenge_id"
            challenge_id = parts[i + 1]
            i += 2
            continue
        out.append(token)
        i += 1
    return out, challenge_id, None


def _status_text(status: dict) -> str:
    if not status.get("enabled"):
        return "off"
    remaining = int(status.get("remaining_seconds", 0))
    return f"on (剩余 {remaining}s)"


def _sudo_action_payload(ctx: "Context") -> dict:
    return {
        "op": "sudo_on",
        "scope": {
            "channel": str(ctx.message.channel),
            "chat_id": str(ctx.message.chat_id),
        },
    }


async def _require_sudo_approval(ctx: "Context", challenge_id: Optional[str]) -> bool:
    manager = ctx.two_factor
    if manager is None:
        await ctx.router._reply(ctx.message, "❌ two-factor manager 不可用")
        return False
    if not bool(getattr(manager, "enabled", False)):
        await ctx.router._reply(ctx.message, "❌ two_factor.enabled=false，无法开启 sudo")
        return False

    action_payload = _sudo_action_payload(ctx)
    if not challenge_id:
        challenge = manager.create_challenge(ctx.user_id, action_payload)
        manager.set_pending_approval_input(ctx.user_id, challenge.challenge_id, "/sudo on")
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "🔐 sudo on 需要 2FA 验证",
                    f"- challenge_id: <code>{challenge.challenge_id}</code>",
                    "请直接回复 6 位验证码。",
                    "若下一条消息不是验证码，将判定失败并结束本次验证。",
                ]
            ),
        )
        return False

    ok, reason = manager.consume_approval(challenge_id, ctx.user_id, action_payload)
    if not ok:
        await ctx.router._reply(ctx.message, f"❌ 2FA 校验失败: <code>{reason}</code>")
        return False
    return True


@command("/sudo", "sys 模式提权开关")
async def handle_sudo(ctx: "Context") -> None:
    runtime_mode = ((ctx.config or {}).get("runtime") or {}).get("mode", "session")
    if not is_system_mode(runtime_mode):
        await ctx.router._reply(ctx.message, "⚠️ 当前实例为 user 模式，/sudo 已禁用")
        return
    if not ctx.auth.is_system_admin(ctx.user_id):
        await ctx.router._reply(ctx.message, "⚠️ 仅 system_admin 可使用 /sudo")
        return

    if getattr(ctx, "system_client", None) is None:
        await ctx.router._reply(
            ctx.message,
            "❌ 当前实例未连接 system_service，sudo 不可用（fail-closed）",
        )
        return

    text = (ctx.message.text or "").strip()
    try:
        parts = shlex.split(text)
    except Exception:
        await ctx.router._reply(ctx.message, "❌ 命令参数解析失败，请检查引号")
        return

    if len(parts) < 2:
        status = ctx.router.get_sudo_status(ctx.user_id, ctx.message.channel, ctx.message.chat_id)
        await ctx.router._reply(ctx.message, f"{_usage()}\n\n当前 sudo: <code>{_status_text(status)}</code>")
        return

    normalized, challenge_id, challenge_err = _extract_challenge(parts)
    if challenge_err:
        await ctx.router._reply(ctx.message, f"❌ {challenge_err}")
        return
    if len(normalized) < 2:
        await ctx.router._reply(ctx.message, _usage())
        return

    sub = normalized[1].strip().lower()
    status = ctx.router.get_sudo_status(ctx.user_id, ctx.message.channel, ctx.message.chat_id)

    if sub == "status":
        await ctx.router._reply(ctx.message, f"当前 sudo: <code>{_status_text(status)}</code>")
        return

    if sub == "off":
        disabled = ctx.router.disable_sudo(ctx.message)
        manager = ctx.two_factor
        if manager is not None:
            try:
                manager.clear_pending_approval_input(ctx.user_id, revoke_challenge=True)
            except Exception:
                pass
        if disabled or status.get("enabled"):
            await ctx.router._reply(ctx.message, "✅ sudo 已关闭")
        else:
            await ctx.router._reply(ctx.message, "ℹ️ sudo 当前已是关闭状态")
        return

    if sub != "on":
        await ctx.router._reply(ctx.message, _usage())
        return

    if status.get("enabled"):
        await ctx.router._reply(
            ctx.message,
            f"ℹ️ sudo 已开启: <code>{_status_text(status)}</code>",
        )
        return

    ok = await _require_sudo_approval(ctx, challenge_id)
    if not ok:
        return

    manager = ctx.two_factor
    ttl_seconds = 600
    if manager is not None:
        ttl_seconds = int(max(1, float(getattr(manager, "approval_grace_seconds", 600))))
        manager.activate_approval_window(
            ctx.user_id,
            ctx.message.channel,
            ctx.message.chat_id,
            ttl_seconds=ttl_seconds,
        )

    state = ctx.router.enable_sudo(ctx.message, ttl_seconds=ttl_seconds)
    remaining = int(state.get("ttl_seconds", ttl_seconds))
    await ctx.router._reply(ctx.message, f"✅ sudo 已开启，剩余 <code>{remaining}</code> 秒")
