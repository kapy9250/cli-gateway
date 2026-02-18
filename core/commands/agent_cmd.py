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
    user_key = str(ctx.message.user_id)
    router._user_agent_pref[user_key] = agent_name

    # Create and activate a new session immediately for better UX.
    target_agent = ctx.agents[agent_name]
    try:
        info = await target_agent.create_session(user_id=ctx.message.user_id, chat_id=ctx.message.chat_id)
    except Exception as e:
        logger.warning("Failed to create new %s session after /agent switch: %s", agent_name, e)
        await router._reply(
            ctx.message,
            f"✅ 已切换到 {agent_name}，但创建会话失败，请发送下一条消息重试",
        )
        return

    agent_config = ctx.config.get("agents", {}).get(agent_name, {})
    model = router._user_model_pref.pop(user_key, None) or agent_config.get("default_model")
    params = agent_config.get("default_params", {}).copy()
    managed = ctx.session_manager.create_session(
        user_id=ctx.message.user_id,
        chat_id=ctx.message.chat_id,
        agent_name=agent_name,
        session_id=info.session_id,
        model=model,
        params=params,
    )
    await router._reply(
        ctx.message,
        f"✅ 已切换到 {agent_name}，当前会话: <code>{managed.session_id}</code>",
    )
