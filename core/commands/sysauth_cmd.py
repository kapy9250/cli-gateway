"""System-auth commands for 2FA challenge, approval, and enrollment flow."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context

try:
    import qrcode
except Exception:  # pragma: no cover - optional dependency fallback
    qrcode = None


def _usage() -> str:
    return "\n".join(
        [
            "用法:",
            "• /sysauth plan &lt;action text&gt;",
            "• /sysauth approve &lt;challenge_id&gt; &lt;totp_code&gt;",
            "• /sysauth status &lt;challenge_id&gt;",
            "• /sysauth setup start",
            "• /sysauth setup verify &lt;totp_code&gt;",
            "• /sysauth setup status",
            "• /sysauth setup cancel",
        ]
    )


def _setup_usage() -> str:
    return "\n".join(
        [
            "用法:",
            "• /sysauth setup start",
            "• /sysauth setup verify &lt;totp_code&gt;",
            "• /sysauth setup status",
            "• /sysauth setup cancel",
        ]
    )


def _get_setup_identity(ctx: "Context", manager) -> tuple[str, str]:
    cfg = (ctx.config or {}).get("two_factor", {})
    issuer = str(cfg.get("issuer", "")).strip() or str(getattr(manager, "issuer", "CLI Gateway"))

    runtime = (ctx.config or {}).get("runtime", {})
    instance_id = str(runtime.get("instance_id", "default")).strip() or "default"
    account_name = f"{instance_id}:{ctx.user_id}"
    return issuer, account_name


async def _send_qr_file(ctx: "Context", otpauth_uri: str) -> bool:
    if qrcode is None:
        return False

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="sysauth-2fa-", suffix=".png")
        os.close(fd)

        def _render() -> None:
            image = qrcode.make(otpauth_uri)
            image.save(tmp_path)

        await asyncio.to_thread(_render)
        await ctx.channel.send_file(
            ctx.message.chat_id,
            tmp_path,
            caption="🔐 2FA 绑定二维码",
        )
        return True
    except Exception:
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def _handle_setup(ctx: "Context", manager) -> None:
    items = (ctx.message.text or "").split()
    if len(items) < 3:
        await ctx.router._reply(ctx.message, _setup_usage())
        return

    action = items[2].strip().lower()
    if action == "start":
        issuer, account_name = _get_setup_identity(ctx, manager)
        enrollment = manager.begin_enrollment(
            user_id=ctx.user_id,
            account_name=account_name,
            issuer=issuer,
        )
        qr_sent = await _send_qr_file(ctx, str(enrollment.get("otpauth_uri", "")))
        now = time.time()
        expires_in = max(0, int(float(enrollment.get("expires_at", now)) - now))

        lines = [
            "🔐 已创建 2FA 绑定会话",
            f"- issuer: <code>{enrollment.get('issuer')}</code>",
            f"- account: <code>{enrollment.get('account_name')}</code>",
            f"- expires_in: <code>{expires_in}</code>",
            f"- reused: <code>{str(bool(enrollment.get('reused'))).lower()}</code>",
            f"- secret: <code>{enrollment.get('secret')}</code>",
            "下一步: /sysauth setup verify &lt;totp_code&gt;",
        ]
        if bool(enrollment.get("already_configured")):
            lines.append("⚠️ 当前用户已有旧绑定，本次 verify 成功后会覆盖旧 secret。")
        if qr_sent:
            lines.append("✅ 已发送二维码文件，请扫码后提交验证码。")
        else:
            lines.append("⚠️ 二维码发送失败，请手动录入 secret 或使用 otpauth URI。")
            lines.append(f"- otpauth: <code>{enrollment.get('otpauth_uri')}</code>")
        await ctx.router._reply(ctx.message, "\n".join(lines))
        return

    if action == "verify":
        if len(items) < 4:
            await ctx.router._reply(ctx.message, "用法: /sysauth setup verify &lt;totp_code&gt;")
            return
        code = items[3].strip()
        ok, reason = manager.verify_enrollment(ctx.user_id, code)
        if not ok:
            await ctx.router._reply(ctx.message, f"❌ 2FA 绑定失败: <code>{reason}</code>")
            return
        await ctx.router._reply(
            ctx.message,
            "✅ 2FA 绑定成功并已保存。后续可使用 /sysauth approve 与 /sys 系统命令。",
        )
        return

    if action == "status":
        st = manager.enrollment_status(ctx.user_id)
        now = time.time()
        expires_at = st.get("pending_expires_at")
        expires_in = max(0, int(float(expires_at) - now)) if expires_at else 0
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "ℹ️ 2FA 绑定状态",
                    f"- configured: <code>{str(bool(st.get('configured'))).lower()}</code>",
                    f"- pending: <code>{str(bool(st.get('pending'))).lower()}</code>",
                    f"- pending_expires_in: <code>{expires_in}</code>",
                ]
            ),
        )
        return

    if action == "cancel":
        removed = manager.cancel_enrollment(ctx.user_id)
        if not removed:
            await ctx.router._reply(ctx.message, "ℹ️ 当前没有待确认的绑定会话。")
            return
        await ctx.router._reply(ctx.message, "✅ 已取消当前 2FA 绑定会话。")
        return

    await ctx.router._reply(ctx.message, _setup_usage())


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
    if sub == "setup":
        await _handle_setup(ctx, manager)
        return

    if sub == "plan":
        if len(parts) < 3 or not parts[2].strip():
            await ctx.router._reply(ctx.message, "用法: /sysauth plan &lt;action text&gt;")
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
                    "下一步: /sysauth approve &lt;challenge_id&gt; &lt;totp_code&gt;",
                ]
            ),
        )
        return

    if sub == "approve":
        # Re-split because code shouldn't be swallowed by maxsplit=2 format.
        items = text.split()
        if len(items) < 4:
            await ctx.router._reply(
                ctx.message,
                "用法: /sysauth approve &lt;challenge_id&gt; &lt;totp_code&gt;",
            )
            return
        challenge_id = items[2].strip()
        code = items[3].strip()
        ok, reason = manager.approve_challenge(challenge_id, ctx.user_id, code)
        if not ok:
            await ctx.router._reply(ctx.message, f"❌ 2FA 审批失败: <code>{reason}</code>")
            return
        window = manager.activate_approval_window(
            ctx.user_id,
            ctx.message.channel,
            ctx.message.chat_id,
        )
        await ctx.router._reply(
            ctx.message,
            f"✅ 2FA 审批通过，本聊天 <code>{window.get('ttl_seconds')}</code> 秒内免挑战",
        )
        return

    if sub == "status":
        items = text.split()
        if len(items) < 3:
            await ctx.router._reply(ctx.message, "用法: /sysauth status &lt;challenge_id&gt;")
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
