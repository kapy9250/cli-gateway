"""Session management commands: /sessions, /current, /switch, /kill, /name."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


@command("/sessions", "列出所有会话")
async def handle_sessions(ctx: "Context") -> None:
    sessions = ctx.session_manager.list_user_sessions(ctx.message.user_id)
    if not sessions:
        await ctx.router._reply(ctx.message, "暂无会话")
        return
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    lines = ["你的会话："]
    for item in sessions:
        marker = "⭐" if current and current.session_id == item.session_id else "-"
        name_suffix = f" [{item.name}]" if getattr(item, "name", None) else ""
        lines.append(f"{marker} {item.session_id} ({item.agent_name}){name_suffix}")
    await ctx.router._reply(ctx.message, "\n".join(lines))


@command("/current", "查看当前会话")
async def handle_current(ctx: "Context") -> None:
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await ctx.router._reply(ctx.message, "当前无活跃会话")
        return
    await ctx.router._reply(
        ctx.message,
        f"当前会话: {current.session_id}\nAgent: {current.agent_name}",
    )


@command("/switch", "切换到指定会话")
async def handle_switch(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    if len(parts) < 2:
        await ctx.router._reply(ctx.message, "用法: /switch <session_id>")
        return
    session_id = parts[1].strip()
    if not ctx.session_manager.switch_session(ctx.message.user_id, session_id):
        await ctx.router._reply(ctx.message, "❌ 会话不存在或无权限")
        return
    await ctx.router._reply(ctx.message, f"✅ 已切换到会话 {session_id}")


@command("/kill", "销毁当前会话")
async def handle_kill(ctx: "Context") -> None:
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await ctx.router._reply(ctx.message, "当前无活跃会话")
        return
    agent = ctx.agents.get(current.agent_name)
    if agent:
        try:
            await agent.destroy_session(current.session_id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Agent session %s already gone, cleaning up metadata only", current.session_id
            )
    ctx.session_manager.destroy_session(current.session_id)
    ctx.router._session_locks.pop(current.session_id, None)
    await ctx.router._reply(ctx.message, f"🗑️ 已销毁会话 {current.session_id}")


@command("/name", "为当前会话命名")
async def handle_name(ctx: "Context") -> None:
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
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
