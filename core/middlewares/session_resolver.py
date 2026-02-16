"""Session resolver middleware — ensures an active agent session exists."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


async def session_resolver_middleware(ctx: "Context", next: Callable[[], Awaitable[None]]) -> None:
    router = ctx.router

    # ── Ensure a managed session exists ──
    current = await _ensure_session(ctx)
    if current is None:
        return

    agent = ctx.agents.get(current.agent_name)
    if agent is None:
        await ctx.channel.send_text(ctx.message.chat_id, f"❌ Agent 不存在: {current.agent_name}")
        return

    # ── Recover if agent lost the session (e.g. after restart) ──
    current = await _recover_stale_session(ctx, agent, current)

    ctx.session = current
    ctx.agent = agent
    await next()


async def _ensure_session(ctx: "Context"):
    """Get existing session or create a new one. Returns ManagedSession or None."""
    router = ctx.router
    message = ctx.message
    current = None

    if message.channel == "email":
        hint = getattr(message, "session_hint", None)
        if hint:
            hinted = ctx.session_manager.get_session(hint)
            if hinted and hinted.user_id == str(message.user_id):
                ctx.session_manager.switch_session(message.user_id, hint)
                current = hinted
                logger.info("Email session resumed via hint: %s", hint)
            else:
                logger.warning("Email session hint %s not found or unauthorized, creating new", hint)
    else:
        current = ctx.session_manager.get_active_session(message.user_id)

    if current is None:
        agent_name = router._get_user_agent(message.user_id)
        agent = ctx.agents.get(agent_name)
        if agent is None:
            await ctx.channel.send_text(
                message.chat_id,
                f"❌ Agent 不可用: {agent_name}，可用: {', '.join(ctx.agents.keys())}",
            )
            return None

        info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
        agent_config = ctx.config["agents"].get(agent_name, {})
        user_key = str(message.user_id)
        model = router._user_model_pref.pop(user_key, None) or agent_config.get("default_model")
        current = ctx.session_manager.create_session(
            user_id=message.user_id,
            chat_id=message.chat_id,
            agent_name=agent_name,
            session_id=info.session_id,
            model=model,
            params=agent_config.get("default_params", {}).copy(),
        )

    return current


async def _recover_stale_session(ctx: "Context", agent, current):
    """If agent lost the session (e.g. after restart), recreate preserving model/params."""
    if agent.get_session_info(current.session_id) is not None:
        return current

    router = ctx.router
    message = ctx.message
    logger.info("Recovering stale session %s, creating new agent session", current.session_id)
    old_model = current.model
    old_params = current.params.copy() if current.params else {}

    ctx.session_manager.destroy_session(current.session_id)
    router._session_locks.pop(current.session_id, None)
    info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
    return ctx.session_manager.create_session(
        user_id=message.user_id,
        chat_id=message.chat_id,
        agent_name=current.agent_name,
        session_id=info.session_id,
        model=old_model,
        params=old_params,
    )
