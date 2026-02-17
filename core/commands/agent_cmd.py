"""Agent command: /agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


@command("/agent", "切换 agent 或查看当前 agent")
async def handle_agent(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    router = ctx.router

    if len(parts) < 2:
        # Show current preference and available agents
        current_pref = router._get_user_agent(ctx.message.user_id)
        current_session = ctx.session_manager.get_active_session(ctx.message.user_id)
        lines = ["<b>Agent 信息：</b>"]
        lines.append(f"默认: {router.default_agent}")
        lines.append(f"当前偏好: {current_pref}")
        if current_session:
            lines.append(f"活跃会话: {current_session.agent_name} ({current_session.session_id})")
        lines.append(f"\n可用 agents: {', '.join(ctx.agents.keys())}")
        lines.append("用法: /agent &lt;name&gt;")
        await router._reply(ctx.message, "\n".join(lines))
        return

    agent_name = parts[1].strip().lower()
    if agent_name not in ctx.agents:
        await router._reply(
            ctx.message,
            f"❌ 未找到 agent: {agent_name}。可用: {', '.join(ctx.agents.keys())}",
        )
        return

    # Record preference
    router._user_agent_pref[str(ctx.message.user_id)] = agent_name

    # Destroy current session so next message auto-creates with new agent
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if current:
        old_agent = ctx.agents.get(current.agent_name)
        if old_agent:
            try:
                await old_agent.destroy_session(current.session_id)
            except Exception:
                logger.warning("Failed to destroy old session %s, ignoring", current.session_id)
        try:
            ctx.session_manager.destroy_session(current.session_id)
        finally:
            router.pop_session_lock(current.session_id)

    await router._reply(
        ctx.message,
        f"✅ 已切换到 {agent_name}，下次发消息时自动创建会话",
    )
