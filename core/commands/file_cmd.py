"""File management commands: /files, /download."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


@command("/files", "列出当前会话输出文件")
async def handle_files(ctx: "Context") -> None:
    router = ctx.router
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await router._reply(ctx.message, "❌ 当前无活跃会话")
        return
    agent = ctx.agents.get(current.agent_name)
    if not agent or current.session_id not in agent.sessions:
        await router._reply(ctx.message, "❌ 会话不可用")
        return
    ai_dir = agent.sessions[current.session_id].work_dir / "ai"
    if not ai_dir.exists():
        await router._reply(ctx.message, "暂无输出文件")
        return
    files = [f.name for f in ai_dir.iterdir() if f.is_file()]
    if not files:
        await router._reply(ctx.message, "暂无输出文件")
        return
    lines = ["📁 输出文件："]
    for fname in sorted(files):
        lines.append(f"- {fname}")
    lines.append("\n使用 /download &lt;filename&gt; 下载")
    await router._reply(ctx.message, "\n".join(lines))


@command("/download", "下载文件")
async def handle_download(ctx: "Context") -> None:
    router = ctx.router
    parts = (ctx.message.text or "").strip().split()
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await router._reply(ctx.message, "❌ 当前无活跃会话")
        return
    if len(parts) < 2:
        await router._reply(ctx.message, "用法: /download &lt;filename&gt;")
        return
    filename = parts[1].strip()
    agent = ctx.agents.get(current.agent_name)
    if not agent or current.session_id not in agent.sessions:
        await router._reply(ctx.message, "❌ 会话不可用")
        return
    ai_dir = agent.sessions[current.session_id].work_dir / "ai"
    filepath = (ai_dir / filename).resolve()
    # Path traversal protection
    if not str(filepath).startswith(str(ai_dir.resolve())):
        await router._reply(ctx.message, "❌ 非法路径")
        return
    if not filepath.exists() or not filepath.is_file():
        await router._reply(ctx.message, f"❌ 未找到文件: {filename}")
        return
    await ctx.channel.send_file(ctx.message.chat_id, str(filepath), caption=filename)
