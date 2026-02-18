"""Agent command: /agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


@command("/agent", "切换 agent 或查看当前 agent")
async def handle_agent(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    router = ctx.router
    scope_id = router.get_scope_id(ctx.message)

    if len(parts) < 2:
        # Show current preference and available agents
        current_pref = router._get_scope_agent(scope_id)
        current_session = ctx.session_manager.get_active_session_for_scope(scope_id)
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

    # Record preference for this chat scope.
    router._set_scope_agent(scope_id, agent_name)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    if current is None:
        await router._reply(
            ctx.message,
            f"✅ 已切换到 {agent_name}，当前作用域暂无会话；发送下一条消息会继续使用该 agent",
        )
        return

    if current.agent_name == agent_name:
        await router._reply(
            ctx.message,
            f"✅ 已是 {agent_name}，会话保持不变: <code>{current.session_id}</code>",
        )
        return

    old_agent_name = current.agent_name
    target_agent = ctx.agents[agent_name]
    existing_work_dir = Path(current.work_dir) if current.work_dir else None
    try:
        info = await target_agent.create_session(
            user_id=ctx.message.user_id,
            chat_id=ctx.message.chat_id,
            session_id=current.session_id,
            work_dir=existing_work_dir,
            scope_dir=router.get_scope_workspace_dir(ctx.message),
        )
    except Exception as e:
        logger.warning("Failed to rebind session %s to %s: %s", current.session_id, agent_name, e)
        await router._reply(
            ctx.message,
            f"❌ 已设置偏好为 {agent_name}，但会话重绑失败，请稍后重试",
        )
        return

    old_agent = ctx.agents.get(old_agent_name)
    if old_agent and old_agent_name != agent_name and current.session_id in old_agent.sessions:
        try:
            await old_agent.destroy_session(current.session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to destroy old %s session %s: %s", old_agent_name, current.session_id, e)

    agent_config = ctx.config.get("agents", {}).get(agent_name, {})
    models = agent_config.get("models", {})
    default_model = agent_config.get("default_model")
    keep_model = current.model if current.model in models else default_model

    supported_params = set(agent_config.get("supported_params", {}).keys())
    params = agent_config.get("default_params", {}).copy()
    for key, value in (current.params or {}).items():
        if key in supported_params:
            params[key] = value

    ctx.session_manager.update_agent(current.session_id, agent_name)
    ctx.session_manager.update_work_dir(current.session_id, str(info.work_dir))
    ctx.session_manager.update_model(current.session_id, keep_model)
    ctx.session_manager.reset_params(current.session_id, params)

    await router._reply(
        ctx.message,
        f"✅ 已切换到 {agent_name}，会话保持不变: <code>{current.session_id}</code>",
    )
