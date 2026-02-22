"""Session management commands: /sessions, /current, /switch, /kill, /name."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.command_registry import command
from utils.runtime_mode import to_external_mode

if TYPE_CHECKING:
    from core.pipeline import Context


logger = logging.getLogger(__name__)


def _runtime_version(ctx: "Context") -> str:
    runtime = (ctx.config or {}).get("runtime", {})
    return str(runtime.get("version", "unknown") or "unknown")


@command("/sessions", "列出所有会话")
async def handle_sessions(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    sessions = ctx.session_manager.list_scope_sessions(scope_id)
    if not sessions:
        await ctx.router._reply(ctx.message, "暂无会话")
        return
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    lines = ["你的会话："]
    for item in sessions:
        marker = "⭐" if current and current.session_id == item.session_id else "-"
        name_suffix = f" [{item.name}]" if getattr(item, "name", None) else ""
        lines.append(f"{marker} {item.session_id} ({item.agent_name}){name_suffix}")
    await ctx.router._reply(ctx.message, "\n".join(lines))


@command("/current", "查看当前会话")
async def handle_current(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    scope_agent = ctx.router._get_scope_agent(scope_id)
    default_agent = str(getattr(ctx.router, "default_agent", "") or "-")
    version = _runtime_version(ctx)
    mode = to_external_mode(((ctx.config or {}).get("runtime") or {}).get("mode", "session"))
    if not current:
        await ctx.router._reply(
            ctx.message,
            (
                "当前无活跃会话\n"
                "Agent: -\n"
                f"下一条将使用: {scope_agent}\n"
                f"默认 Agent: {default_agent}\n"
                f"模式: <code>{mode}</code>\n"
                f"版本: <code>{version}</code>"
            ),
        )
        return
    await ctx.router._reply(
        ctx.message,
        (
            f"当前会话: {current.session_id}\n"
            f"Agent: {current.agent_name}\n"
            f"作用域偏好 Agent: {scope_agent}\n"
            f"模式: <code>{mode}</code>\n"
            f"版本: <code>{version}</code>"
        ),
    )


@command("/switch", "切换到指定会话")
async def handle_switch(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    scope_id = ctx.router.get_scope_id(ctx.message)
    if len(parts) < 2:
        await ctx.router._reply(ctx.message, "用法: /switch <session_id>")
        return
    session_id = parts[1].strip()
    if not ctx.session_manager.switch_session_for_scope(scope_id, session_id):
        await ctx.router._reply(ctx.message, "❌ 会话不存在或无权限")
        return
    await ctx.router._reply(ctx.message, f"✅ 已切换到会话 {session_id}")


@command("/kill", "销毁当前会话")
async def handle_kill(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    if not current:
        await ctx.router._reply(ctx.message, "当前无活跃会话")
        return
    agent = ctx.agents.get(current.agent_name)
    if agent:
        try:
            await agent.destroy_session(current.session_id)
        except Exception as e:
            logger.warning(
                "Failed to destroy agent session %s: %s, cleaning up metadata only", current.session_id, e
            )
    try:
        ctx.session_manager.destroy_session(current.session_id)
    finally:
        ctx.router.pop_session_lock(current.session_id)
    await ctx.router._reply(ctx.message, f"🗑️ 已销毁会话 {current.session_id}")


@command("/name", "为当前会话命名")
async def handle_name(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    if not current:
        await ctx.router._reply(ctx.message, "❌ 当前无活跃会话")
        return
    parts = (ctx.message.text or "").strip().split()
    if len(parts) < 2:
        await ctx.router._reply(ctx.message, "用法: /name &lt;label&gt;")
        return
    name = " ".join(parts[1:]).strip()
    ctx.session_manager.update_name(current.session_id, name)
    await ctx.router._reply(ctx.message, f"✅ 会话已命名: {name}")
