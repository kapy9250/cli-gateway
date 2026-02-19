"""Memory management commands: /memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


def _memory_disabled_text() -> str:
    return "❌ 记忆系统未启用（请在配置中开启 memory.enabled）"


@command("/memory", "管理长期记忆")
async def handle_memory(ctx: "Context") -> None:
    manager = getattr(ctx, "memory_manager", None)
    if manager is None or not bool(getattr(manager, "enabled", False)):
        await ctx.router._reply(ctx.message, _memory_disabled_text())
        return

    text = (ctx.message.text or "").strip()
    parts = text.split()
    if len(parts) == 1:
        user_stats_method = getattr(manager, "user_stats", None)
        if callable(user_stats_method):
            stats = await user_stats_method(user_id=str(ctx.user_id))
            user_items = int(stats.get("user_items", 0))
        else:
            stats = await manager.health_stats()
            user_items = int(stats.get("total_items", 0))
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "🧠 记忆系统",
                    f"- my_items: <code>{user_items}</code>",
                    f"- vector_supported: <code>{str(bool(stats.get('vector_supported'))).lower()}</code>",
                    "",
                    "用法：",
                    "memory list [short|mid|long|all] [limit]",
                    "memory find <query>",
                    "memory show <id>",
                    "memory note <text>",
                    "memory pin <id>",
                    "memory unpin <id>",
                    "memory forget <id>",
                ]
            ),
        )
        return

    sub = parts[1].strip().lower()
    user_id = str(ctx.user_id)
    scope_id = ctx.router.get_scope_id(ctx.message)
    channel = str(ctx.message.channel)

    if sub == "list":
        tier = "all"
        if len(parts) >= 3:
            tier = parts[2].strip().lower()
        limit = 15
        if len(parts) >= 4 and parts[3].isdigit():
            limit = max(1, min(50, int(parts[3])))
        rows = await manager.list_memories(user_id=user_id, tier=tier, limit=limit)
        if not rows:
            await ctx.router._reply(ctx.message, "暂无记忆")
            return
        lines = [f"📚 记忆列表（tier={tier}）"]
        for row in rows:
            flag = "📌" if row.pinned else "-"
            lines.append(f"{flag} #{row.memory_id} ({row.tier}|{row.domain}/{row.topic}) {row.summary[:100]}")
        await ctx.router._reply(ctx.message, "\n".join(lines))
        return

    if sub == "find":
        query = " ".join(parts[2:]).strip()
        if not query:
            await ctx.router._reply(ctx.message, "用法: /memory find <query>")
            return
        rows = await manager.search_memories(user_id=user_id, query=query, limit=8)
        if not rows:
            await ctx.router._reply(ctx.message, "未检索到相关记忆")
            return
        lines = [f"🔎 检索结果: {query}"]
        for row in rows:
            score = f"{float(row.score):.3f}"
            lines.append(f"- #{row.memory_id} ({row.tier}|{row.domain}/{row.topic}|score={score}) {row.summary[:100]}")
        await ctx.router._reply(ctx.message, "\n".join(lines))
        return

    if sub == "show":
        if len(parts) < 3 or not parts[2].isdigit():
            await ctx.router._reply(ctx.message, "用法: /memory show <id>")
            return
        memory_id = int(parts[2])
        row = await manager.get_memory(user_id=user_id, memory_id=memory_id)
        if row is None:
            await ctx.router._reply(ctx.message, "❌ 记忆不存在或无权限")
            return
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    f"🧾 记忆 #{row.memory_id}",
                    f"- tier: <code>{row.tier}</code>",
                    f"- type: <code>{row.memory_type}</code>",
                    f"- tree: <code>{row.domain}/{row.topic}/{row.item}</code>",
                    f"- pinned: <code>{str(bool(row.pinned)).lower()}</code>",
                    f"- summary: {row.summary}",
                    "",
                    f"{row.content[:1800]}",
                ]
            ),
        )
        return

    if sub == "note":
        payload = " ".join(parts[2:]).strip()
        if not payload:
            await ctx.router._reply(ctx.message, "用法: /memory note <text>")
            return
        memory_id = await manager.add_note(
            user_id=user_id,
            scope_id=scope_id,
            session_id=getattr(getattr(ctx, "session", None), "session_id", None),
            channel=channel,
            text=payload,
        )
        if not memory_id:
            await ctx.router._reply(ctx.message, "❌ 写入失败（可能命中敏感信息规则）")
            return
        await ctx.router._reply(ctx.message, f"✅ 已保存记忆 #{memory_id}")
        return

    if sub in {"pin", "unpin"}:
        if len(parts) < 3 or not parts[2].isdigit():
            await ctx.router._reply(ctx.message, f"用法: /memory {sub} <id>")
            return
        ok = await manager.set_pinned(user_id=user_id, memory_id=int(parts[2]), pinned=(sub == "pin"))
        if not ok:
            await ctx.router._reply(ctx.message, "❌ 操作失败（记忆不存在或无权限）")
            return
        await ctx.router._reply(ctx.message, "✅ 已更新")
        return

    if sub == "forget":
        if len(parts) < 3 or not parts[2].isdigit():
            await ctx.router._reply(ctx.message, "用法: /memory forget <id>")
            return
        ok = await manager.forget_memory(user_id=user_id, memory_id=int(parts[2]))
        if not ok:
            await ctx.router._reply(ctx.message, "❌ 删除失败（记忆不存在或无权限）")
            return
        await ctx.router._reply(ctx.message, "✅ 已删除")
        return

    if sub in {"share", "skills"}:
        await ctx.router._reply(ctx.message, "❌ 跨用户共享已禁用")
        return

    await ctx.router._reply(ctx.message, "❌ 未知子命令，发送 /memory 查看帮助")
