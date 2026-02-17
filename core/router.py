"""Message router — thin wrapper around the middleware pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

from agents.base import BaseAgent
from channels.base import BaseChannel, IncomingMessage
from core.auth import Auth
from core.billing import BillingTracker
from core.formatter import OutputFormatter
from core.pipeline import Context, Pipeline
from core.rules import RulesLoader
from core.session import SessionManager
from utils.constants import MAX_ATTACHMENT_SIZE_BYTES

# Importing commands triggers @command decorators → populates the registry
import core.commands  # noqa: F401

logger = logging.getLogger(__name__)


class Router:
    """Route incoming messages through a middleware pipeline."""

    def __init__(
        self,
        auth: Auth,
        session_manager: SessionManager,
        agents: Dict[str, BaseAgent],
        channel: BaseChannel,
        config: dict,
        billing: Optional[BillingTracker] = None,
        two_factor: Optional[object] = None,
        system_executor: Optional[object] = None,
        audit_logger: Optional[object] = None,
    ) -> None:
        self.auth = auth
        self.session_manager = session_manager
        self.agents = agents
        self.channel = channel
        self.config = config
        self.billing = billing
        self.two_factor = two_factor
        self.system_executor = system_executor
        self.audit_logger = audit_logger
        self.rules_loader = RulesLoader()
        self.formatter = OutputFormatter(config.get("formatter", {}))

        configured_default = config.get("default_agent", "codex")
        self.default_agent = (
            configured_default if configured_default in agents else next(iter(agents.keys()))
        )
        if self.default_agent != configured_default:
            logger.warning(
                "Configured default_agent '%s' not available, falling back to '%s'",
                configured_default,
                self.default_agent,
            )

        self._user_agent_pref: Dict[str, str] = {}
        self._user_model_pref: Dict[str, str] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}  # session_id → cancel signal

        self.pipeline = self._build_pipeline()

    # ── Pipeline construction ──────────────────────────────────

    def _build_pipeline(self) -> Pipeline:
        from core.middlewares.logging_mw import logging_middleware
        from core.middlewares.auth_mw import auth_middleware
        from core.middlewares.mode_guard import mode_guard_middleware
        from core.middlewares.command_parser import command_parser_middleware
        from core.middlewares.session_resolver import session_resolver_middleware
        from core.middlewares.agent_dispatcher import agent_dispatcher_middleware

        return Pipeline(
            [
                logging_middleware,
                auth_middleware,
                mode_guard_middleware,
                command_parser_middleware,
                session_resolver_middleware,
                agent_dispatcher_middleware,
            ]
        )

    # ── Public entry point ─────────────────────────────────────

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle one normalized incoming message."""
        ctx = Context(
            message=message,
            channel_name=message.channel,
            user_id=str(message.user_id),
            router=self,
            auth=self.auth,
            session_manager=self.session_manager,
            agents=self.agents,
            channel=self.channel,
            billing=self.billing,
            two_factor=self.two_factor,
            system_executor=self.system_executor,
            audit_logger=self.audit_logger,
            formatter=self.formatter,
            config=self.config,
        )
        try:
            await self.pipeline.execute(ctx)
        except Exception:
            logger.error("Unhandled error processing message from user=%s", message.user_id, exc_info=True)
            try:
                await self.channel.send_text(message.chat_id, "❌ 内部错误，请稍后重试")
            except Exception:
                pass  # channel itself might be broken

    # ── Helpers (used by middlewares and commands) ──────────────

    @staticmethod
    def _fmt(channel: str, text: str) -> str:
        """Convert lightweight HTML markup to channel-appropriate format."""
        if channel == "telegram":
            return text
        t = text
        t = re.sub(r"<b>(.*?)</b>", r"**\1**", t)
        t = re.sub(r"<code>(.*?)</code>", r"`\1`", t)
        t = t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return t

    async def _reply(self, message: IncomingMessage, text: str) -> None:
        """Send a formatted reply, auto-converting markup for the channel."""
        await self.channel.send_text(message.chat_id, self._fmt(message.channel, text))

    def _get_user_agent(self, user_id: str) -> str:
        """Return the user's preferred agent name, falling back to global default."""
        return self._user_agent_pref.get(str(user_id), self.default_agent)

    def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock."""
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def pop_session_lock(self, session_id: str) -> None:
        """Remove a per-session lock if present."""
        self._session_locks.pop(session_id, None)

    def get_cancel_event(self, session_id: str) -> asyncio.Event:
        """Get or create a per-session cancel event."""
        return self._cancel_events.setdefault(session_id, asyncio.Event())

    def peek_cancel_event(self, session_id: str) -> Optional[asyncio.Event]:
        """Get an existing per-session cancel event without creating it."""
        return self._cancel_events.get(session_id)

    def pop_cancel_event(self, session_id: str) -> None:
        """Remove a per-session cancel event if present."""
        self._cancel_events.pop(session_id, None)

    async def _prepare_prompt(self, message: IncomingMessage, agent: BaseAgent, current) -> str:
        """Build the final prompt: text + filtered attachments + channel context."""
        prompt = message.text

        # Reject oversized attachments
        if message.attachments:
            rejected = []
            accepted = []
            for att in message.attachments:
                if att.size_bytes and att.size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
                    rejected.append(f"{att.filename} ({att.size_bytes // 1024 // 1024}MB)")
                else:
                    accepted.append(att)
            if rejected:
                limit_mb = MAX_ATTACHMENT_SIZE_BYTES // 1024 // 1024
                await self.channel.send_text(
                    message.chat_id,
                    f"⚠️ 以下附件超过 {limit_mb}MB 限制，已跳过：\n" + "\n".join(f"- {r}" for r in rejected),
                )
            message.attachments = accepted

        # Copy accepted attachments to session workspace
        if message.attachments:
            user_dir = BaseAgent.get_user_upload_dir(agent.sessions[current.session_id].work_dir)
            att_lines = []
            for att in message.attachments:
                safe_name = Path(att.filename).name
                dest = BaseAgent.safe_filename(user_dir, safe_name)
                try:
                    shutil.copy2(att.filepath, dest)
                    att_lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
                    att_lines.append(f"  Path: {dest}")
                except Exception as e:
                    logger.warning("Failed to copy attachment %s: %s", att.filename, e)
                    att_lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
                    att_lines.append(f"  Path: {att.filepath}")
            att_info = "\n".join(att_lines)
            prompt = f"{prompt}\n\n附件:\n{att_info}" if prompt else f"附件:\n{att_info}"

        # Prepend channel context + sender context
        channel_context = self.rules_loader.get_system_prompt(message.channel)
        sender_context = (
            "[SENDER CONTEXT]\n"
            f"- sender_user_id: {message.user_id}\n"
            f"- sender_username: {message.sender_username or 'unknown'}\n"
            f"- sender_display_name: {message.sender_display_name or 'unknown'}\n"
            f"- sender_mention_token: {message.sender_mention or 'unknown'}\n"
            "- Reply behavior constraint: start replies by mentioning the sender. "
            "If the task semantics clearly require notifying additional people, mention them too.\n"
            "[END SENDER CONTEXT]\n\n"
        )
        if prompt:
            prompt = f"{channel_context}{sender_context}{prompt}"

        return prompt

    def _record_usage(self, message: IncomingMessage, agent: BaseAgent, current, response: str) -> None:
        """Record billing and email session log after response delivery."""
        if self.billing:
            usage = agent.get_last_usage(current.session_id)
            if usage:
                self.billing.record(
                    session_id=current.session_id,
                    user_id=str(message.user_id),
                    channel=message.channel,
                    agent_name=current.agent_name,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    cost_usd=usage.cost_usd,
                    duration_ms=usage.duration_ms,
                )

        if message.channel == "email" and hasattr(self.channel, "save_session_log"):
            try:
                self.channel.save_session_log(
                    sender_addr=message.user_id,
                    session_id=current.session_id,
                    prompt=message.text or "",
                    response=response,
                )
            except Exception as e:
                logger.warning("Failed to save email session log: %s", e)
