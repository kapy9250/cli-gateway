"""Message router for command parsing and agent forwarding."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

import shutil
from agents.base import BaseAgent, SessionInfo
from channels.base import BaseChannel, IncomingMessage
from core.auth import Auth
from core.billing import BillingTracker
from core.rules import RulesLoader
from core.session import SessionManager
from utils.constants import GATEWAY_COMMANDS, MAX_ATTACHMENT_SIZE_BYTES, STREAM_UPDATE_INTERVAL

logger = logging.getLogger(__name__)


class Router:
    """Route incoming messages to commands or current active session."""

    def __init__(
        self,
        auth: Auth,
        session_manager: SessionManager,
        agents: Dict[str, BaseAgent],
        channel: BaseChannel,
        config: dict,
        billing: Optional[BillingTracker] = None,
    ) -> None:
        self.auth = auth
        self.session_manager = session_manager
        self.agents = agents
        self.channel = channel
        self.config = config
        self.billing = billing
        self.rules_loader = RulesLoader()
        configured_default = config.get('default_agent', 'codex')
        self.default_agent = configured_default if configured_default in agents else next(iter(agents.keys()))
        if self.default_agent != configured_default:
            logger.warning("Configured default_agent '%s' not available, falling back to '%s'",
                           configured_default, self.default_agent)
        self._user_agent_pref: Dict[str, str] = {}
        self._user_model_pref: Dict[str, str] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _fmt(channel: str, text: str) -> str:
        """Convert lightweight markup to channel-appropriate format.

        Source uses Telegram HTML: <b>, <code>, &lt;, &gt;.
        For non-Telegram channels, convert to Markdown equivalents.
        """
        if channel == "telegram":
            return text
        # Discord / Email / others → Markdown
        import re
        t = text
        t = re.sub(r'<b>(.*?)</b>', r'**\1**', t)
        t = re.sub(r'<code>(.*?)</code>', r'`\1`', t)
        t = t.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        return t

    async def _reply(self, message: IncomingMessage, text: str) -> None:
        """Send a formatted reply, auto-converting markup for the channel."""
        await self.channel.send_text(message.chat_id, self._fmt(message.channel, text))

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle one normalized incoming message."""
        if not self.auth.check(str(message.user_id), channel=message.channel):
            logger.warning("Unauthorized access: user_id=%s channel=%s", message.user_id, message.channel)
            await self._reply(message, "⚠️ 未授权访问")
            return

        text = (message.text or "").strip()

        # Support "kapy <subcommand>" format
        if text.lower().startswith("kapy "):
            # Convert to slash command format
            subcommand = text[5:].strip()  # Remove "kapy "
            if subcommand:
                # Create a modified message with "/" prefix
                modified_message = IncomingMessage(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                    text=f"/{subcommand}",
                    is_private=message.is_private,
                    is_reply_to_bot=message.is_reply_to_bot,
                    is_mention_bot=message.is_mention_bot,
                    reply_to_text=message.reply_to_text,
                    attachments=message.attachments
                )
                await self._handle_command(modified_message)
                return
            else:
                await self._reply(message, "用法: kapy &lt;command&gt; [args]\n发送 'kapy help' 查看帮助")
                return

        if text.startswith("/"):
            await self._handle_command(message)
            return

        await self._forward_to_agent(message)

    async def _handle_command(self, message: IncomingMessage) -> None:
        text = (message.text or "").strip()
        parts = text.split()
        command = parts[0].split("@")[0].lower()

        # If not a gateway command, forward to agent
        if command not in GATEWAY_COMMANDS:
            logger.info(f"Forwarding command {command} to agent")
            await self._forward_to_agent(message)
            return

        if command == "/start":
            await self._reply(message, "👋 CLI Gateway 已启动，发送 /help 查看命令。")
            return

        if command == "/help":
            await self._reply(
                message,
                "\n".join(
                    [
                        "📚 可用命令：",
                        "",
                        "💡 <b>两种格式</b>",
                        "• 传统: <code>/model opus</code>",
                        "• 新格式: <code>kapy model opus</code>",
                        "",
                        "<b>会话管理</b>",
                        "agent [&lt;name&gt;] - 切换 agent 或查看当前 agent",
                        "sessions - 列出所有会话",
                        "current - 查看当前会话",
                        "switch &lt;id&gt; - 切换到指定会话",
                        "kill - 销毁当前会话",
                        "",
                        "<b>模型配置</b>",
                        "model [&lt;alias&gt;] - 切换模型或查看可用模型",
                        "param [&lt;key&gt; &lt;value&gt;] - 设置参数或查看可用参数",
                        "params - 查看当前配置",
                        "reset - 重置为默认配置",
                        "",
                        "<b>示例</b>",
                        "<code>kapy model opus</code>",
                        "<code>kapy param thinking high</code>",
                        "<code>kapy params</code>",
                    ]
                ),
            )
            return

        if command == "/agent":
            if len(parts) < 2:
                # Show current preference and available agents
                current_pref = self._get_user_agent(message.user_id)
                current_session = self.session_manager.get_active_session(message.user_id)
                lines = ["<b>Agent 信息：</b>"]
                lines.append(f"默认: {self.default_agent}")
                lines.append(f"当前偏好: {current_pref}")
                if current_session:
                    lines.append(f"活跃会话: {current_session.agent_name} ({current_session.session_id})")
                lines.append(f"\n可用 agents: {', '.join(self.agents.keys())}")
                lines.append("用法: /agent &lt;name&gt;")
                await self._reply(message, "\n".join(lines))
                return

            agent_name = parts[1].strip().lower()
            if agent_name not in self.agents:
                await self._reply(
                    message,
                    f"❌ 未找到 agent: {agent_name}。可用: {', '.join(self.agents.keys())}",
                )
                return

            # Record preference (decoupled from session creation)
            self._user_agent_pref[str(message.user_id)] = agent_name

            # Destroy current session so next message auto-creates with new agent
            current = self.session_manager.get_active_session(message.user_id)
            if current:
                old_agent = self.agents.get(current.agent_name)
                if old_agent:
                    try:
                        await old_agent.destroy_session(current.session_id)
                    except Exception:
                        logger.warning("Failed to destroy old session %s, ignoring", current.session_id)
                self.session_manager.destroy_session(current.session_id)

            await self._reply(
                message,
                f"✅ 已切换到 {agent_name}，下次发消息时自动创建会话",
            )
            return

        if command == "/sessions":
            sessions = self.session_manager.list_user_sessions(message.user_id)
            if not sessions:
                await self._reply(message, "暂无会话")
                return

            current = self.session_manager.get_active_session(message.user_id)
            lines = ["你的会话："]
            for item in sessions:
                marker = "⭐" if current and current.session_id == item.session_id else "-"
                lines.append(f"{marker} {item.session_id} ({item.agent_name})")
            await self._reply(message, "\n".join(lines))
            return

        if command == "/current":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self._reply(message, "当前无活跃会话")
                return
            await self._reply(
                message,
                f"当前会话: {current.session_id}\nAgent: {current.agent_name}",
            )
            return

        if command == "/switch":
            if len(parts) < 2:
                await self._reply(message, "用法: /switch <session_id>")
                return

            session_id = parts[1].strip()
            if not self.session_manager.switch_session(message.user_id, session_id):
                await self._reply(message, "❌ 会话不存在或无权限")
                return

            await self._reply(message, f"✅ 已切换到会话 {session_id}")
            return

        if command == "/kill":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self._reply(message, "当前无活跃会话")
                return

            agent = self.agents.get(current.agent_name)
            if agent:
                try:
                    await agent.destroy_session(current.session_id)
                except Exception:
                    logger.warning("Agent session %s already gone, cleaning up metadata only", current.session_id)
            self.session_manager.destroy_session(current.session_id)
            await self._reply(message, f"🗑️ 已销毁会话 {current.session_id}")
            return

        if command == "/model":
            current = self.session_manager.get_active_session(message.user_id)
            # Determine which agent's models to show/use
            active_agent_name = current.agent_name if current else self._get_user_agent(message.user_id)
            agent_config = self.config['agents'].get(active_agent_name, {})
            models = agent_config.get('models', {})

            if len(parts) < 2:
                # Show available models
                if models:
                    current_model = current.model if current else None
                    lines = [f"<b>{active_agent_name} 可用模型：</b>"]
                    for alias, full_name in models.items():
                        marker = "✅" if current_model == alias else "-"
                        lines.append(f"{marker} <code>{alias}</code> ({full_name})")
                    await self._reply(message, "\n".join(lines))
                else:
                    await self._reply(message, "该 agent 无可切换模型")
                return

            model_alias = parts[1].strip().lower()

            if model_alias not in models:
                await self._reply(
                    message,
                    f"❌ 模型不存在: {model_alias}\n可用: {', '.join(models.keys())}"
                )
                return

            if current:
                self.session_manager.update_model(current.session_id, model_alias)
                await self._reply(
                    message,
                    f"✅ 已切换模型: {model_alias} ({models[model_alias]})"
                )
            else:
                # No active session — store preference, will be applied on next auto-create
                self._user_model_pref[str(message.user_id)] = model_alias
                await self._reply(
                    message,
                    f"✅ 已设置模型偏好: {model_alias} ({models[model_alias]})，下次会话生效"
                )
            return

        if command == "/param":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self._reply(message, "❌ 当前无活跃会话")
                return

            if len(parts) < 2:
                # Show supported params
                agent_config = self.config['agents'].get(current.agent_name, {})
                supported = agent_config.get('supported_params', {})
                if supported:
                    lines = [f"<b>{current.agent_name} 支持的参数：</b>"]
                    for key in supported.keys():
                        current_value = current.params.get(key, "(未设置)")
                        lines.append(f"- <code>{key}</code>: {current_value}")
                    lines.append("\n用法: /param &lt;key&gt; &lt;value&gt;")
                    await self._reply(message, "\n".join(lines))
                else:
                    await self._reply(message, "该 agent 无可配置参数")
                return

            if len(parts) < 3:
                await self._reply(message, "用法: /param &lt;key&gt; &lt;value&gt;")
                return

            key = parts[1].strip()
            value = parts[2].strip()

            agent_config = self.config['agents'].get(current.agent_name, {})
            supported = agent_config.get('supported_params', {})

            if key not in supported:
                await self._reply(
                    message,
                    f"❌ {current.agent_name} 不支持参数 {key}\n支持: {', '.join(supported.keys())}"
                )
                return

            self.session_manager.update_param(current.session_id, key, value)
            await self._reply(message, f"✅ 已设置 {key} = {value}")
            return

        if command == "/params":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self._reply(message, "❌ 当前无活跃会话")
                return

            agent_config = self.config['agents'].get(current.agent_name, {})
            models = agent_config.get('models', {})

            lines = [
                f"<b>当前配置</b>",
                f"会话: <code>{current.session_id}</code>",
                f"Agent: {current.agent_name}",
            ]

            if current.model:
                model_full = models.get(current.model, current.model)
                lines.append(f"模型: <code>{current.model}</code> ({model_full})")
            else:
                lines.append(f"模型: (默认)")

            if current.params:
                lines.append("\n<b>参数：</b>")
                for key, value in current.params.items():
                    lines.append(f"- <code>{key}</code>: {value}")
            else:
                lines.append("\n参数: (无)")

            await self._reply(message, "\n".join(lines))
            return

        if command == "/reset":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self._reply(message, "❌ 当前无活跃会话")
                return

            agent_config = self.config['agents'].get(current.agent_name, {})
            default_model = agent_config.get('default_model')
            default_params = agent_config.get('default_params', {}).copy()

            self.session_manager.update_model(current.session_id, default_model)
            self.session_manager.reset_params(current.session_id, default_params)

            await self._reply(message, "✅ 已重置为默认配置")
            return

        # Unknown command: forward to agent
        await self._forward_to_agent(message)

    async def _forward_to_agent(self, message: IncomingMessage) -> None:
        """Route a user message to the active agent session."""
        current = await self._ensure_session(message)
        if current is None:
            return

        agent = self.agents.get(current.agent_name)
        if agent is None:
            await self.channel.send_text(message.chat_id, f"❌ Agent 不存在: {current.agent_name}")
            return

        # Recover stale sessions (agent restarted but session metadata preserved)
        current = await self._recover_stale_session(message, agent, current)

        # Acquire per-session lock to prevent concurrent CLI invocations
        session_id = current.session_id
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        lock = self._session_locks[session_id]

        if lock.locked():
            await self.channel.send_text(message.chat_id, "⏳ 上一个请求还在处理中，请稍后再试")
            return

        async with lock:
            await self._cleanup_orphan_busy(agent, current.session_id)
            prompt = await self._prepare_prompt(message, agent, current)
            await self.channel.send_typing(message.chat_id)

            if message.channel == "email" and hasattr(self.channel, 'set_reply_session'):
                self.channel.set_reply_session(message.chat_id, current.session_id)

            response = await self._deliver_response(message, agent, current, prompt)
            self.session_manager.touch(current.session_id)
            self._record_usage(message, agent, current, response)

    # ── _forward_to_agent sub-steps ──

    def _get_user_agent(self, user_id: str) -> str:
        """Return the user's preferred agent name, falling back to global default."""
        return self._user_agent_pref.get(str(user_id), self.default_agent)

    async def _ensure_session(self, message: IncomingMessage):
        """Get existing session or create a new one. Returns ManagedSession or None."""
        current = None

        if message.channel == "email":
            hint = getattr(message, 'session_hint', None)
            if hint:
                hinted = self.session_manager.get_session(hint)
                if hinted and hinted.user_id == str(message.user_id):
                    self.session_manager.switch_session(message.user_id, hint)
                    current = hinted
                    logger.info("Email session resumed via hint: %s", hint)
                else:
                    logger.warning("Email session hint %s not found or unauthorized, creating new", hint)
        else:
            current = self.session_manager.get_active_session(message.user_id)

        if current is None:
            agent_name = self._get_user_agent(message.user_id)
            agent = self.agents.get(agent_name)
            if agent is None:
                await self.channel.send_text(message.chat_id, f"❌ Agent 不可用: {agent_name}，可用: {', '.join(self.agents.keys())}")
                return None

            info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
            agent_config = self.config['agents'].get(agent_name, {})
            # Use stored model preference if set, otherwise config default
            user_key = str(message.user_id)
            model = self._user_model_pref.pop(user_key, None) or agent_config.get('default_model')
            current = self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=agent_name,
                session_id=info.session_id,
                model=model,
                params=agent_config.get('default_params', {}).copy(),
            )

        return current

    async def _recover_stale_session(self, message, agent, current):
        """If agent lost the session (e.g. after restart), recreate it preserving model/params."""
        if agent.get_session_info(current.session_id) is not None:
            return current

        logger.info("Recovering stale session %s, creating new agent session", current.session_id)
        old_model = current.model
        old_params = current.params.copy() if current.params else {}

        self.session_manager.destroy_session(current.session_id)
        info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
        return self.session_manager.create_session(
            user_id=message.user_id,
            chat_id=message.chat_id,
            agent_name=current.agent_name,
            session_id=info.session_id,
            model=old_model,
            params=old_params,
        )

    async def _cleanup_orphan_busy(self, agent, session_id: str) -> None:
        """Reset busy flag if subprocess died without clearing it."""
        session_info = agent.get_session_info(session_id)
        if session_info and session_info.is_busy:
            if hasattr(agent, 'is_process_alive') and not agent.is_process_alive(session_id):
                logger.warning("Session %s marked busy but process is dead, cleaning up", session_id)
                if hasattr(agent, 'kill_process'):
                    await agent.kill_process(session_id)
                else:
                    session_info.is_busy = False

    async def _prepare_prompt(self, message, agent, current) -> str:
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

        # Prepend channel context
        channel_context = self.rules_loader.get_system_prompt(message.channel)
        if channel_context and prompt:
            prompt = f"{channel_context}{prompt}"

        return prompt

    async def _deliver_response(self, message, agent, current, prompt: str) -> str:
        """Send prompt to agent and relay response to channel (streaming or batch)."""
        import time as _time

        use_streaming = getattr(self.channel, 'supports_streaming', True)
        buffer = ""

        if use_streaming:
            message_id = None
            last_update_time = 0

            async for chunk in agent.send_message(
                current.session_id, prompt, model=current.model, params=current.params
            ):
                if chunk:
                    buffer += chunk
                    current_time = _time.time()
                    if current_time - last_update_time >= STREAM_UPDATE_INTERVAL:
                        if message_id is None:
                            message_id = await self.channel.send_text(message.chat_id, buffer or "⏳ 处理中...")
                        else:
                            await self.channel.edit_message(message.chat_id, message_id, buffer)
                        last_update_time = current_time

            response = buffer.strip() or "✅ 完成"
            if message_id is None:
                await self.channel.send_text(message.chat_id, response)
            else:
                await self.channel.edit_message(message.chat_id, message_id, response)
        else:
            async for chunk in agent.send_message(
                current.session_id, prompt, model=current.model, params=current.params
            ):
                if chunk:
                    buffer += chunk
            response = buffer.strip() or "✅ 完成"
            await self.channel.send_text(message.chat_id, response)

        return response

    def _record_usage(self, message, agent, current, response: str) -> None:
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

        if message.channel == "email" and hasattr(self.channel, 'save_session_log'):
            try:
                self.channel.save_session_log(
                    sender_addr=message.user_id,
                    session_id=current.session_id,
                    prompt=message.text or "",
                    response=response,
                )
            except Exception as e:
                logger.warning("Failed to save email session log: %s", e)
