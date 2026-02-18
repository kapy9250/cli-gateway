"""Session resolver middleware — ensures an active agent session exists."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context
    from core.session import ManagedSession

logger = logging.getLogger(__name__)


async def session_resolver_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    router = ctx.router

    # ── Ensure a managed session exists ──
    current = await _ensure_session(ctx)
    if current is None:
        return

    agent = ctx.agents.get(current.agent_name)
    if agent is None:
        await ctx.router._reply(ctx.message, f"❌ Agent 不存在: {current.agent_name}")
        return

    # ── Recover if agent lost the session (e.g. after restart) ──
    current = await _recover_stale_session(ctx, agent, current)
    if current is None:
        return

    ctx.session = current
    ctx.agent = agent
    await call_next()


async def _ensure_session(ctx: "Context") -> Optional["ManagedSession"]:
    """Get existing session or create a new one. Returns ManagedSession or None."""
    router = ctx.router
    message = ctx.message
    scope_id = router.get_scope_id(message)
    current = None

    if message.channel == "email":
        hint = getattr(message, "session_hint", None)
        if hint:
            hinted = ctx.session_manager.get_session(hint)
            if hinted and hinted.scope_id == scope_id:
                ctx.session_manager.switch_session_for_scope(scope_id, hint)
                current = hinted
                logger.info("Email session resumed via hint: %s", hint)
            elif hinted and hinted.user_id == str(message.user_id) and (
                hinted.scope_id.startswith("legacy:user:") or hinted.scope_id == str(message.user_id)
            ):
                ctx.session_manager.assign_scope(hint, scope_id, activate=True)
                current = hinted
                logger.info("Email session migrated to scoped key via hint: %s", hint)
            else:
                logger.warning("Email session hint %s not found or unauthorized, creating new", hint)

    if current is None:
        current = ctx.session_manager.get_active_session_for_scope(scope_id)

    if current is None:
        # One-time migration for pre-scope persisted data.
        legacy_active = ctx.session_manager.get_active_session(message.user_id)
        if legacy_active and legacy_active.user_id == str(message.user_id) and (
            legacy_active.scope_id.startswith("legacy:user:") or legacy_active.scope_id == str(message.user_id)
        ):
            ctx.session_manager.assign_scope(legacy_active.session_id, scope_id, activate=True)
            current = ctx.session_manager.get_session(legacy_active.session_id)

    if current is None:
        agent_name = router._get_scope_agent(scope_id)
        agent = ctx.agents.get(agent_name)
        if agent is None:
            await ctx.router._reply(
                message,
                f"❌ Agent 不可用: {agent_name}，可用: {', '.join(ctx.agents.keys())}",
            )
            return None

        info = await agent.create_session(
            user_id=message.user_id,
            chat_id=message.chat_id,
            scope_dir=router.get_scope_workspace_dir(message),
        )
        agent_config = ctx.config["agents"].get(agent_name, {})
        model = router._pop_scope_model_pref(scope_id) or agent_config.get("default_model")
        current = ctx.session_manager.create_session(
            user_id=message.user_id,
            chat_id=message.chat_id,
            agent_name=agent_name,
            scope_id=scope_id,
            session_id=info.session_id,
            work_dir=str(info.work_dir),
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
    logger.info("Recovering stale session %s in-place", current.session_id)

    work_dir = Path(current.work_dir) if current.work_dir else None
    try:
        info = await agent.create_session(
            user_id=message.user_id,
            chat_id=message.chat_id,
            session_id=current.session_id,
            work_dir=work_dir,
            scope_dir=router.get_scope_workspace_dir(message),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to recover stale session %s: %s", current.session_id, e)
        await ctx.router._reply(message, "❌ 会话恢复失败，请稍后重试")
        return None

    ctx.session_manager.update_work_dir(current.session_id, str(info.work_dir))
    ctx.session_manager.touch(current.session_id)
    return ctx.session_manager.get_session(current.session_id) or current
