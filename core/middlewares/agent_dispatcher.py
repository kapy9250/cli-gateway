"""Agent dispatcher middleware — forwards prompt to agent, streams response."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TYPE_CHECKING

from core.streaming_delivery import StreamingDelivery
from utils.constants import MAX_HISTORY_ENTRIES

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


async def agent_dispatcher_middleware(ctx: "Context", call_next: Callable[[], Awaitable[None]]) -> None:
    router = ctx.router
    session = ctx.session
    agent = ctx.agent
    message = ctx.message
    session_id = session.session_id

    # ── Acquire per-session lock ──
    lock = router.get_session_lock(session_id)

    if lock.locked():
        await ctx.router._reply(message, "⏳ 上一个请求还在处理中，请稍后再试")
        return

    async with lock:
        await _cleanup_orphan_busy(agent, session_id)
        prompt = await router._prepare_prompt(message, agent, session)
        await ctx.channel.send_typing(message.chat_id)

        if message.channel == "email" and hasattr(ctx.channel, "set_reply_session"):
            ctx.channel.set_reply_session(message.chat_id, session_id)

        # Record user prompt in history
        ctx.session_manager.add_history(session_id, "user", message.text or "", MAX_HISTORY_ENTRIES, persist=False)

        response = ""
        run_as_root = bool(router.is_sudo_enabled(message))
        try:
            delivery = StreamingDelivery(ctx.formatter)
            response = await delivery.deliver(
                ctx,
                agent.send_message(
                    session_id,
                    prompt,
                    model=session.model,
                    params=session.params,
                    run_as_root=run_as_root,
                ),
                session_id=session_id,
            )
        except Exception as e:
            logger.error("Agent error: %s", e, exc_info=True)
            response = "❌ 处理请求时出错，请稍后重试"
            await ctx.router._reply(message, response)

        ctx.response = response

        # Record assistant response
        ctx.session_manager.add_history(session_id, "assistant", response or "", MAX_HISTORY_ENTRIES, persist=False)
        ctx.session_manager.touch(session_id)
        if getattr(ctx, "memory_manager", None) is not None:
            try:
                await ctx.memory_manager.capture_turn(
                    user_id=str(message.user_id),
                    scope_id=ctx.router.get_scope_id(message),
                    session_id=session_id,
                    channel=str(message.channel),
                    user_text=message.text or "",
                    assistant_text=response or "",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to capture memory for session %s: %s", session_id, e)
        router._record_usage(message, agent, session, response or "")


async def _cleanup_orphan_busy(agent, session_id: str) -> None:
    """Reset busy flag if subprocess died without clearing it."""
    session_info = agent.get_session_info(session_id)
    if session_info and session_info.is_busy:
        if hasattr(agent, "is_process_alive") and not agent.is_process_alive(session_id):
            logger.warning("Session %s marked busy but process is dead, cleaning up", session_id)
            if hasattr(agent, "kill_process"):
                await agent.kill_process(session_id)
            else:
                session_info.is_busy = False
