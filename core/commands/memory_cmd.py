"""Memory management commands: /memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


def _memory_disabled_text() -> str:
    return "❌ 记忆系统未启用（请在配置中开启 memory.enabled）"


def _pct(value: float) -> str:
    return f"{max(0.0, float(value)) * 100:.1f}%"


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
                    "memory fb <request_id> <good|bad> [note]",
                    "memory metrics [days]",
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
        session_id = getattr(getattr(ctx, "session", None), "session_id", None)
        req_id = None
        search_with_event = getattr(manager, "search_memories_with_event", None)
        if callable(search_with_event):
            rows, req_id = await search_with_event(
                user_id=user_id,
                query=query,
                session_id=session_id,
                channel=channel,
                limit=8,
            )
        else:
            rows = await manager.search_memories(user_id=user_id, query=query, limit=8)
        if not rows:
            if req_id is None:
                await ctx.router._reply(ctx.message, "未检索到相关记忆")
            else:
                await ctx.router._reply(
                    ctx.message,
                    f"未检索到相关记忆\n- request_id: <code>{req_id}</code>（可反馈：/memory fb {req_id} bad）",
                )
            return
        lines = [f"🔎 检索结果: {query}"]
        if req_id is not None:
            lines.append(f"- request_id: <code>{req_id}</code>")
        for row in rows:
            score = f"{float(row.score):.3f}"
            lines.append(f"- #{row.memory_id} ({row.tier}|{row.domain}/{row.topic}|score={score}) {row.summary[:100]}")
        if req_id is not None:
            lines.append(f"- 反馈: /memory fb {req_id} good|bad [note]")
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

    if sub in {"fb", "feedback"}:
        if len(parts) < 4 or not parts[2].isdigit():
            await ctx.router._reply(ctx.message, "用法: /memory fb <request_id> <good|bad> [note]")
            return
        feedback = str(parts[3]).strip().lower()
        note = " ".join(parts[4:]).strip() if len(parts) >= 5 else None
        record_feedback = getattr(manager, "record_retrieval_feedback", None)
        if not callable(record_feedback):
            await ctx.router._reply(ctx.message, "❌ 当前记忆后端不支持反馈记录")
            return
        ok = await record_feedback(
            user_id=user_id,
            retrieval_id=int(parts[2]),
            feedback=feedback,
            note=note or None,
        )
        if not ok:
            await ctx.router._reply(ctx.message, "❌ 反馈失败（request_id 不存在、无权限或反馈值非法）")
            return
        await ctx.router._reply(ctx.message, "✅ 已记录反馈")
        return

    if sub in {"metrics", "stats"}:
        days = 7
        if len(parts) >= 3 and parts[2].isdigit():
            days = max(1, min(90, int(parts[2])))
        metrics_fn = getattr(manager, "retrieval_stats", None)
        if not callable(metrics_fn):
            await ctx.router._reply(ctx.message, "❌ 当前记忆后端不支持检索指标")
            return
        stats = await metrics_fn(user_id=user_id, days=days)
        total = int(stats.get("total_queries", 0))
        lines = [
            f"📈 记忆检索指标（近 {days} 天）",
            f"- total_queries: <code>{total}</code>",
            f"- hit_rate: <code>{_pct(stats.get('hit_rate', 0.0))}</code>",
            f"- context_inject_rate: <code>{_pct(stats.get('context_inject_rate', 0.0))}</code>",
            f"- avg_result_count: <code>{float(stats.get('avg_result_count', 0.0)):.2f}</code>",
            f"- avg_latency_ms: <code>{float(stats.get('avg_latency_ms', 0.0)):.1f}</code>",
            f"- vector_usage_rate: <code>{_pct(stats.get('vector_usage_rate', 0.0))}</code>",
            f"- feedback_coverage: <code>{_pct(stats.get('feedback_coverage', 0.0))}</code>",
            f"- positive_feedback_rate: <code>{_pct(stats.get('positive_feedback_rate', 0.0))}</code>",
        ]
        recent_fn = getattr(manager, "recent_retrieval_events", None)
        if callable(recent_fn):
            recent = await recent_fn(user_id=user_id, limit=5)
            if recent:
                lines.append("")
                lines.append("最近请求：")
                for ev in recent:
                    fb = ev.feedback or "-"
                    lines.append(
                        f"- req#{ev.retrieval_id} hits={ev.result_count} inj={str(bool(ev.context_injected)).lower()} "
                        f"fb={fb} q={ev.query[:40]}"
                    )
        await ctx.router._reply(ctx.message, "\n".join(lines))
        return

    if sub in {"share", "skills"}:
        await ctx.router._reply(ctx.message, "❌ 跨用户共享已禁用")
        return

    await ctx.router._reply(ctx.message, "❌ 未知子命令，发送 /memory 查看帮助")
