"""Runtime mode guard middleware for system-level command routing."""

from __future__ import annotations

from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context


SYSTEM_COMMAND_PREFIXES = (
    "/sysauth",
    "/sys",
    "/sudo",
    "/system",
    "/docker",
    "/cron",
    "/journal",
    "/config",
)


def _normalize_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.lower().startswith("kapy "):
        sub = text[5:].strip()
        if sub:
            return f"/{sub}"
    return text


def _is_system_command(cmd_name: str) -> bool:
    return any(
        cmd_name == prefix or cmd_name.startswith(prefix + ".")
        for prefix in SYSTEM_COMMAND_PREFIXES
    )


async def mode_guard_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    text = _normalize_text(ctx.message.text or "")
    if not text.startswith("/"):
        await call_next()
        return

    cmd_name = text.split()[0].split("@")[0].lower()
    if not _is_system_command(cmd_name):
        await call_next()
        return

    if cmd_name == "/sys" or cmd_name.startswith("/sys."):
        await ctx.router._reply(
            ctx.message,
            "⚠️ /sys 指令已下线，请使用 `/sudo on` 开启 2FA 授权后直接下发自然语言任务",
        )
        return

    runtime_mode = ((ctx.config or {}).get("runtime") or {}).get("mode", "session")
    if str(runtime_mode).lower() not in {"system", "sys"}:
        await ctx.router._reply(ctx.message, "⚠️ 当前实例为 user 模式，系统级命令已禁用")
        return

    if not ctx.auth.is_system_admin(ctx.user_id):
        await ctx.router._reply(ctx.message, "⚠️ 仅 system_admin 可执行系统级命令")
        return

    await call_next()
