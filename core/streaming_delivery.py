"""Streaming delivery: relay agent output chunks to a channel."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Optional, TYPE_CHECKING

from core.formatter import OutputFormatter
from utils.constants import STREAM_UPDATE_INTERVAL

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


class StreamingDelivery:
    """Deliver agent output chunks to the channel.

    Two modes:
    * **streaming** (Telegram / Discord): send an initial message, then
      periodically ``edit_message`` with the growing buffer.
    * **batch** (Email): accumulate all chunks, send once.

    Supports:
    * Idle timeout — if no new chunk arrives for *idle_timeout* seconds
      the stream is considered stalled and delivery stops.
    * Cancel event — ``router.get_cancel_event(session_id)`` can be set
      from a ``/cancel`` command to interrupt the stream.
    """

    def __init__(self, formatter: OutputFormatter) -> None:
        self.formatter = formatter

    async def deliver(
        self,
        ctx: "Context",
        chunks: AsyncIterator[str],
        session_id: str,
        idle_timeout: float = 300.0,
    ) -> str:
        """Stream *chunks* to the channel and return the full response text."""

        # Get (or create) a cancel event shared via the Router so that
        # a /cancel command on a separate message can signal this stream.
        cancel_event: asyncio.Event = ctx.router.get_cancel_event(session_id)

        use_streaming = getattr(ctx.channel, "supports_streaming", True)

        if use_streaming:
            response = await self._stream_mode(ctx, chunks, cancel_event, idle_timeout)
        else:
            response = await self._batch_mode(ctx, chunks, cancel_event, idle_timeout)

        # Clean up cancel event after delivery
        ctx.router.pop_cancel_event(session_id)

        return response

    # ── private helpers ────────────────────────────────────────

    async def _stream_mode(
        self,
        ctx: "Context",
        chunks: AsyncIterator[str],
        cancel_event: asyncio.Event,
        idle_timeout: float,
    ) -> str:
        buffer = ""
        message_id = None
        last_update = 0.0
        update_interval = float(getattr(ctx.channel, "stream_update_interval", STREAM_UPDATE_INTERVAL))
        if update_interval < 0:
            update_interval = 0.0
        chunk_iter = chunks.__aiter__()
        while True:
            if cancel_event.is_set():
                logger.info("Stream cancelled for user=%s", ctx.user_id)
                break

            try:
                chunk = await asyncio.wait_for(chunk_iter.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning("Stream idle timeout (%.0fs) for user=%s", idle_timeout, ctx.user_id)
                break

            now = time.time()
            if chunk:
                buffer += chunk
                if now - last_update >= update_interval:
                    if message_id is None:
                        message_id = await ctx.channel.send_text(
                            ctx.message.chat_id,
                            ctx.router.format_outbound_text(ctx.message, buffer or "⏳ 处理中..."),
                        )
                    else:
                        await ctx.channel.edit_message(
                            ctx.message.chat_id,
                            message_id,
                            ctx.router.format_outbound_text(ctx.message, buffer),
                        )
                    last_update = now

        response = self.formatter.clean(buffer) or "✅ 完成"
        response_with_state = ctx.router.format_outbound_text(ctx.message, response)
        parts = self.formatter.split_message(response_with_state)

        if message_id is None:
            await ctx.channel.send_text(ctx.message.chat_id, parts[0])
        else:
            await ctx.channel.edit_message(ctx.message.chat_id, message_id, parts[0])

        for part in parts[1:]:
            await ctx.channel.send_text(ctx.message.chat_id, part)

        return response

    async def _batch_mode(
        self,
        ctx: "Context",
        chunks: AsyncIterator[str],
        cancel_event: asyncio.Event,
        idle_timeout: float,
    ) -> str:
        buffer = ""
        chunk_iter = chunks.__aiter__()
        while True:
            if cancel_event.is_set():
                break

            try:
                chunk = await asyncio.wait_for(chunk_iter.__anext__(), timeout=idle_timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning("Stream idle timeout (%.0fs) for user=%s", idle_timeout, ctx.user_id)
                break

            if chunk:
                buffer += chunk

        response = self.formatter.clean(buffer) or "✅ 完成"
        response_with_state = ctx.router.format_outbound_text(ctx.message, response)
        for part in self.formatter.split_message(response_with_state):
            await ctx.channel.send_text(ctx.message.chat_id, part)

        return response
