"""Logging middleware — records request arrival and processing time."""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline import Context

logger = logging.getLogger(__name__)


async def logging_middleware(ctx: "Context", next: Callable[[], Awaitable[None]]) -> None:
    text_preview = (ctx.message.text or "")[:60]
    logger.info(
        "Message from user=%s channel=%s: %s",
        ctx.user_id,
        ctx.channel_name,
        text_preview,
    )
    start = time.time()
    try:
        await next()
    finally:
        elapsed = time.time() - start
        logger.info(
            "Processed user=%s in %.2fs response_len=%d",
            ctx.user_id,
            elapsed,
            len(ctx.response),
        )
