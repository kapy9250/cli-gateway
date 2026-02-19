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
from core.session_scope import build_scope_id, build_scope_workspace_dir
from utils.constants import MAX_ATTACHMENT_SIZE_BYTES
from utils.runtime_mode import is_system_mode

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
        memory_manager: Optional[object] = None,
        two_factor: Optional[object] = None,
        system_executor: Optional[object] = None,
        system_client: Optional[object] = None,
        system_grant: Optional[object] = None,
        sudo_state: Optional[object] = None,
        audit_logger: Optional[object] = None,
    ) -> None:
        self.auth = auth
        self.session_manager = session_manager
        self.agents = agents
        self.channel = channel
        self.config = config
        self.billing = billing
        self.memory_manager = memory_manager
        self.two_factor = two_factor
        self.system_executor = system_executor
        self.system_client = system_client
        self.system_grant = system_grant
        self.sudo_state = sudo_state
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

        self._scope_agent_pref: Dict[str, str] = {}
        self._scope_model_pref: Dict[str, str] = {}
        # Backward compatibility for existing call sites/tests.
        self._user_agent_pref = self._scope_agent_pref
        self._user_model_pref = self._scope_model_pref
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}  # session_id → cancel signal

        self.pipeline = self._build_pipeline()

    # ── Pipeline construction ──────────────────────────────────

    def _build_pipeline(self) -> Pipeline:
        from core.middlewares.logging_mw import logging_middleware
        from core.middlewares.auth_mw import auth_middleware
        from core.middlewares.mode_guard import mode_guard_middleware
        from core.middlewares.two_factor_reply import two_factor_reply_middleware
        from core.middlewares.command_parser import command_parser_middleware
        from core.middlewares.session_resolver import session_resolver_middleware
        from core.middlewares.agent_dispatcher import agent_dispatcher_middleware

        return Pipeline(
            [
                logging_middleware,
                auth_middleware,
                mode_guard_middleware,
                two_factor_reply_middleware,
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
            memory_manager=self.memory_manager,
            agents=self.agents,
            channel=self.channel,
            billing=self.billing,
            two_factor=self.two_factor,
            system_executor=self.system_executor,
            system_client=self.system_client,
            system_grant=self.system_grant,
            sudo_state=self.sudo_state,
            audit_logger=self.audit_logger,
            formatter=self.formatter,
            config=self.config,
        )
        try:
            await self.pipeline.execute(ctx)
        except Exception:
            logger.error("Unhandled error processing message from user=%s", message.user_id, exc_info=True)
            try:
                await self.channel.send_text(
                    message.chat_id,
                    self.format_outbound_text(message, "❌ 内部错误，请稍后重试"),
                )
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
        await self.channel.send_text(message.chat_id, self.format_outbound_text(message, text))

    def _sudo_footer(self, message: IncomingMessage) -> str:
        if not is_system_mode(((self.config or {}).get("runtime") or {}).get("mode")):
            return ""
        status = self.get_sudo_status(message.user_id, message.channel, message.chat_id)
        state = "on" if status.get("enabled") else "off"
        return f"\n\nsudo: <code>{state}</code>"

    def format_outbound_text(self, message: IncomingMessage, text: str) -> str:
        body = str(text or "")
        body += self._sudo_footer(message)
        return self._fmt(message.channel, body)

    def get_sudo_status(self, user_id: str, channel: str, chat_id: str) -> dict:
        manager = self.sudo_state
        if manager is None:
            return {"enabled": False, "remaining_seconds": 0, "expires_at": None}
        try:
            return manager.status(user_id=str(user_id), channel=str(channel), chat_id=str(chat_id))
        except Exception:
            return {"enabled": False, "remaining_seconds": 0, "expires_at": None}

    def is_sudo_enabled(self, message: IncomingMessage) -> bool:
        if not is_system_mode(((self.config or {}).get("runtime") or {}).get("mode")):
            return False
        status = self.get_sudo_status(message.user_id, message.channel, message.chat_id)
        return bool(status.get("enabled", False))

    def enable_sudo(self, message: IncomingMessage, ttl_seconds: int = 600) -> dict:
        manager = self.sudo_state
        if manager is None:
            return {"enabled": False, "remaining_seconds": 0, "expires_at": None}
        return manager.enable(
            user_id=str(message.user_id),
            channel=str(message.channel),
            chat_id=str(message.chat_id),
            ttl_seconds=int(ttl_seconds),
        )

    def disable_sudo(self, message: IncomingMessage) -> bool:
        manager = self.sudo_state
        if manager is None:
            return False
        return bool(
            manager.disable(
                user_id=str(message.user_id),
                channel=str(message.channel),
                chat_id=str(message.chat_id),
            )
        )

    def get_scope_id(self, message: IncomingMessage) -> str:
        """Return scope key for this incoming message."""
        return build_scope_id(message)

    def get_scope_workspace_dir(self, message: IncomingMessage) -> str:
        """Return per-scope workspace subdirectory."""
        return build_scope_workspace_dir(message)

    def _get_scope_agent(self, scope_id: str) -> str:
        """Return agent preference for scope, fallback to default."""
        scope_key = str(scope_id)
        agent_name = self._scope_agent_pref.get(scope_key)
        if agent_name:
            return agent_name
        dm_user = self._extract_dm_user(scope_key)
        if dm_user:
            legacy = self._scope_agent_pref.get(dm_user)
            if legacy:
                return legacy
        return self.default_agent

    def _set_scope_agent(self, scope_id: str, agent_name: str) -> None:
        self._scope_agent_pref[str(scope_id)] = str(agent_name)

    def _get_scope_model_pref(self, scope_id: str) -> Optional[str]:
        scope_key = str(scope_id)
        model = self._scope_model_pref.get(scope_key)
        if model is not None:
            return model
        dm_user = self._extract_dm_user(scope_key)
        if dm_user:
            return self._scope_model_pref.get(dm_user)
        return None

    def _set_scope_model_pref(self, scope_id: str, model: str) -> None:
        self._scope_model_pref[str(scope_id)] = str(model)

    def _pop_scope_model_pref(self, scope_id: str) -> Optional[str]:
        scope_key = str(scope_id)
        if scope_key in self._scope_model_pref:
            return self._scope_model_pref.pop(scope_key, None)
        dm_user = self._extract_dm_user(scope_key)
        if dm_user:
            return self._scope_model_pref.pop(dm_user, None)
        return None

    @staticmethod
    def _extract_dm_user(scope_id: str) -> Optional[str]:
        parts = str(scope_id).split(":", 2)
        if len(parts) == 3 and parts[1] == "dm":
            return parts[2]
        return None

    def _get_user_agent(self, user_id: str) -> str:
        """Legacy helper: treats user_id as preference key."""
        return self._get_scope_agent(str(user_id))

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
                await self._reply(
                    message,
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
        memory_context = ""
        if self.memory_manager is not None:
            try:
                memory_context = await self.memory_manager.build_memory_context(
                    user_id=str(message.user_id),
                    query=message.text or "",
                    session_id=getattr(current, "session_id", None),
                    channel=str(message.channel),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to inject memory context: %s", e)

        if prompt:
            prompt = f"{channel_context}{sender_context}{memory_context}{prompt}"

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
