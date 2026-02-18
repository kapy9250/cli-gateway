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
    two_factor: Optional[object]
    sudo_state: Optional[object]
    system_executor: Optional[object]
    system_client: Optional[object]
    system_grant: Optional[object]
    audit_logger: Optional[object]
    formatter: OutputFormatter
    config: dict

    # ── Mutable working state (set by middlewares) ──
    session: Optional["ManagedSession"] = None
    agent: Optional["BaseAgent"] = None
    response: str = ""


# Type alias for a middleware function
Middleware = Callable[[Context, Callable[[], Awaitable[None]]], Awaitable[None]]


def _make_handler(mw: Middleware, next_handler: Callable, ctx: Context) -> Callable:
    """Create a handler closure that calls *mw* with *next_handler*.

    Using a named function (instead of a lambda in a loop) avoids the
    classic late-binding closure bug.
    """

    async def handler() -> None:
        await mw(ctx, next_handler)

    return handler


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
            handler = _make_handler(mw, handler, ctx)

        await handler()
