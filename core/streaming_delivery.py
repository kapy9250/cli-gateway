"""Streaming delivery: relay agent output chunks to a channel."""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator, TYPE_CHECKING

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
    * Cancel event — ``ctx.cancel_event`` can be set from a ``/cancel``
      command to interrupt the stream.
    """

    def __init__(self, formatter: OutputFormatter) -> None:
        self.formatter = formatter

    async def deliver(
        self,
        ctx: "Context",
        chunks: AsyncIterator[str],
        idle_timeout: float = 300.0,
    ) -> str:
        """Stream *chunks* to the channel and return the full response text."""

        use_streaming = getattr(ctx.channel, "supports_streaming", True)
        buffer = ""

        if use_streaming:
            response = await self._stream_mode(ctx, chunks, buffer, idle_timeout)
        else:
            response = await self._batch_mode(ctx, chunks, buffer, idle_timeout)

        return response

    # ── private helpers ────────────────────────────────────────

    async def _stream_mode(
        self, ctx: "Context", chunks: AsyncIterator[str], buffer: str, idle_timeout: float
    ) -> str:
        message_id = None
        last_update = 0.0

        async for chunk in chunks:
            if ctx.cancel_event.is_set():
                logger.info("Stream cancelled for user=%s", ctx.user_id)
                break
            if chunk:
                buffer += chunk
                now = time.time()
                if now - last_update >= STREAM_UPDATE_INTERVAL:
                    if message_id is None:
                        message_id = await ctx.channel.send_text(
                            ctx.message.chat_id, buffer or "⏳ 处理中..."
                        )
                    else:
                        await ctx.channel.edit_message(ctx.message.chat_id, message_id, buffer)
                    last_update = now

        response = self.formatter.clean(buffer) or "✅ 完成"
        parts = self.formatter.split_message(response)

        if message_id is None:
            await ctx.channel.send_text(ctx.message.chat_id, parts[0])
        else:
            await ctx.channel.edit_message(ctx.message.chat_id, message_id, parts[0])

        for part in parts[1:]:
            await ctx.channel.send_text(ctx.message.chat_id, part)

        return response

    async def _batch_mode(
        self, ctx: "Context", chunks: AsyncIterator[str], buffer: str, idle_timeout: float
    ) -> str:
        async for chunk in chunks:
            if ctx.cancel_event.is_set():
                break
            if chunk:
                buffer += chunk

        response = self.formatter.clean(buffer) or "✅ 完成"
        for part in self.formatter.split_message(response):
            await ctx.channel.send_text(ctx.message.chat_id, part)

        return response
