"""Middleware pipeline engine with request Context."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional

from channels.base import BaseChannel, IncomingMessage
from core.auth import Auth
from core.billing import BillingTracker
from core.formatter import OutputFormatter
from core.session import SessionManager

if TYPE_CHECKING:
    from agents.base import BaseAgent
    from core.session import ManagedSession

logger = logging.getLogger(__name__)


@dataclass
class Context:
    """Per-request context flowing through the middleware pipeline."""

    # ── Immutable request data ──
    message: IncomingMessage
    channel_name: str
    user_id: str

    # ── Component references (injected by Router) ──
    router: object  # Router instance — avoids circular import
    auth: Auth
    session_manager: SessionManager
    agents: Dict[str, "BaseAgent"]
    channel: BaseChannel
    billing: Optional[BillingTracker]
    formatter: OutputFormatter
    config: dict

    # ── Mutable working state (set by middlewares) ──
    session: Optional["ManagedSession"] = None
    agent: Optional["BaseAgent"] = None
    response: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


# Type alias for a middleware function
Middleware = Callable[[Context, Callable[[], Awaitable[None]]], Awaitable[None]]


class Pipeline:
    """Execute an ordered list of middlewares as an onion (nested) chain."""

    def __init__(self, middlewares: List[Middleware]) -> None:
        self.middlewares = middlewares

    async def execute(self, ctx: Context) -> None:
        """Run the middleware chain for *ctx*."""

        async def _noop() -> None:
            """Terminal handler — does nothing."""

        # Build nested closures from right to left so that
        # mw[0] wraps mw[1] wraps … wraps _noop.
        handler = _noop
        for mw in reversed(self.middlewares):
            # Capture *mw* and *handler* in the closure's default args
            # to avoid the classic late-binding issue.
            async def _wrap(ctx: Context, _mw=mw, _next=handler) -> None:
                await _mw(ctx, _next)

            handler = lambda _ctx=ctx, _w=_wrap: _w(_ctx)  # noqa: E731

        try:
            await handler()
        except Exception:
            logger.error("Pipeline error for user=%s", ctx.user_id, exc_info=True)
            raise
