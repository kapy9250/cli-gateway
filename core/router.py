"""Message router for command parsing and agent forwarding."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict

import shutil
from agents.base import BaseAgent, SessionInfo
from channels.base import BaseChannel, IncomingMessage
from core.auth import Auth
from core.rules import RulesLoader
from core.session import SessionManager

logger = logging.getLogger(__name__)

# Gateway commands (intercepted by Router)
# All other commands (like /status, /thinking, etc.) are forwarded to the agent
GATEWAY_COMMANDS = {
    '/start',
    '/help',
    '/agent',
    '/sessions',
    '/kill',
    '/current',
    '/switch',
    '/model',
    '/param',
    '/params',
    '/reset',
}


class Router:
    """Route incoming messages to commands or current active session."""

    def __init__(
        self,
        auth: Auth,
        session_manager: SessionManager,
        agents: Dict[str, BaseAgent],
        channel: BaseChannel,
        config: dict,
    ) -> None:
        self.auth = auth
        self.session_manager = session_manager
        self.agents = agents
        self.channel = channel
        self.config = config
        self.rules_loader = RulesLoader()
        self.default_agent = next(iter(agents.keys()), "claude")
        self._session_locks: Dict[str, asyncio.Lock] = {}

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle one normalized incoming message."""
        if not self.auth.check(str(message.user_id), channel=message.channel):
            logger.warning("Unauthorized access: user_id=%s channel=%s", message.user_id, message.channel)
            await self.channel.send_text(message.chat_id, "⚠️ 未授权访问")
            return

        text = (message.text or "").strip()
        
        # Support "kapybara <subcommand>" format
        if text.lower().startswith("kapybara "):
            # Convert to slash command format
            subcommand = text[9:].strip()  # Remove "kapybara "
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
                await self.channel.send_text(message.chat_id, "用法: kapybara &lt;command&gt; [args]\n发送 'kapybara help' 查看帮助")
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
            await self.channel.send_text(message.chat_id, "👋 CLI Gateway 已启动，发送 /help 查看命令。")
            return

        if command == "/help":
            await self.channel.send_text(
                message.chat_id,
                "\n".join(
                    [
                        "📚 可用命令：",
                        "",
                        "💡 <b>两种格式</b>",
                        "• 传统: <code>/model opus</code>",
                        "• 新格式: <code>kapybara model opus</code>",
                        "",
                        "<b>会话管理</b>",
                        "agent &lt;name&gt; - 切换 agent（claude/codex/gemini）",
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
                        "<code>kapybara model opus</code>",
                        "<code>kapybara param thinking high</code>",
                        "<code>kapybara params</code>",
                    ]
                ),
            )
            return

        if command == "/agent":
            if len(parts) < 2:
                await self.channel.send_text(message.chat_id, "用法: /agent <name>")
                return

            agent_name = parts[1].strip().lower()
            agent = self.agents.get(agent_name)
            if agent is None:
                await self.channel.send_text(
                    message.chat_id,
                    f"❌ 未找到 agent: {agent_name}。可用: {', '.join(self.agents.keys())}",
                )
                return

            info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
            
            # Get default model and params from config
            agent_config = self.config['agents'].get(agent_name, {})
            default_model = agent_config.get('default_model')
            default_params = agent_config.get('default_params', {}).copy()
            
            self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=agent_name,
                session_id=info.session_id,
                model=default_model,
                params=default_params,
            )
            await self.channel.send_text(
                message.chat_id,
                f"✅ 已切换到 {agent_name}，当前会话: {info.session_id}",
            )
            return

        if command == "/sessions":
            sessions = self.session_manager.list_user_sessions(message.user_id)
            if not sessions:
                await self.channel.send_text(message.chat_id, "暂无会话")
                return

            current = self.session_manager.get_active_session(message.user_id)
            lines = ["你的会话："]
            for item in sessions:
                marker = "⭐" if current and current.session_id == item.session_id else "-"
                lines.append(f"{marker} {item.session_id} ({item.agent_name})")
            await self.channel.send_text(message.chat_id, "\n".join(lines))
            return

        if command == "/current":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "当前无活跃会话")
                return
            await self.channel.send_text(
                message.chat_id,
                f"当前会话: {current.session_id}\nAgent: {current.agent_name}",
            )
            return

        if command == "/switch":
            if len(parts) < 2:
                await self.channel.send_text(message.chat_id, "用法: /switch <session_id>")
                return

            session_id = parts[1].strip()
            if not self.session_manager.switch_session(message.user_id, session_id):
                await self.channel.send_text(message.chat_id, "❌ 会话不存在或无权限")
                return

            await self.channel.send_text(message.chat_id, f"✅ 已切换到会话 {session_id}")
            return

        if command == "/kill":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "当前无活跃会话")
                return

            agent = self.agents.get(current.agent_name)
            if agent:
                await agent.destroy_session(current.session_id)
            self.session_manager.destroy_session(current.session_id)
            await self.channel.send_text(message.chat_id, f"🗑️ 已销毁会话 {current.session_id}")
            return

        if command == "/model":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "❌ 当前无活跃会话")
                return

            if len(parts) < 2:
                # Show available models
                agent_config = self.config['agents'].get(current.agent_name, {})
                models = agent_config.get('models', {})
                if models:
                    lines = [f"<b>{current.agent_name} 可用模型：</b>"]
                    for alias, full_name in models.items():
                        marker = "✅" if current.model == alias else "-"
                        lines.append(f"{marker} <code>{alias}</code> ({full_name})")
                    await self.channel.send_text(message.chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(message.chat_id, "该 agent 无可切换模型")
                return

            model_alias = parts[1].strip().lower()
            agent_config = self.config['agents'].get(current.agent_name, {})
            models = agent_config.get('models', {})
            
            if model_alias not in models:
                await self.channel.send_text(
                    message.chat_id,
                    f"❌ 模型不存在: {model_alias}\n可用: {', '.join(models.keys())}"
                )
                return

            self.session_manager.update_model(current.session_id, model_alias)
            await self.channel.send_text(
                message.chat_id,
                f"✅ 已切换模型: {model_alias} ({models[model_alias]})"
            )
            return

        if command == "/param":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "❌ 当前无活跃会话")
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
                    await self.channel.send_text(message.chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(message.chat_id, "该 agent 无可配置参数")
                return

            if len(parts) < 3:
                await self.channel.send_text(message.chat_id, "用法: /param &lt;key&gt; &lt;value&gt;")
                return

            key = parts[1].strip()
            value = parts[2].strip()

            agent_config = self.config['agents'].get(current.agent_name, {})
            supported = agent_config.get('supported_params', {})

            if key not in supported:
                await self.channel.send_text(
                    message.chat_id,
                    f"❌ {current.agent_name} 不支持参数 {key}\n支持: {', '.join(supported.keys())}"
                )
                return

            self.session_manager.update_param(current.session_id, key, value)
            await self.channel.send_text(message.chat_id, f"✅ 已设置 {key} = {value}")
            return

        if command == "/params":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "❌ 当前无活跃会话")
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
            
            await self.channel.send_text(message.chat_id, "\n".join(lines))
            return

        if command == "/reset":
            current = self.session_manager.get_active_session(message.user_id)
            if not current:
                await self.channel.send_text(message.chat_id, "❌ 当前无活跃会话")
                return

            agent_config = self.config['agents'].get(current.agent_name, {})
            default_model = agent_config.get('default_model')
            default_params = agent_config.get('default_params', {}).copy()

            self.session_manager.update_model(current.session_id, default_model)
            self.session_manager.reset_params(current.session_id, default_params)

            await self.channel.send_text(message.chat_id, "✅ 已重置为默认配置")
            return

        # Unknown command: forward to agent
        await self._forward_to_agent(message)

    async def _forward_to_agent(self, message: IncomingMessage) -> None:
        current = None

        # Email channel: session routing via session_hint from email thread
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
            # No hint = new email conversation → always create new session
        else:
            # Telegram / Discord: use active session as before
            current = self.session_manager.get_active_session(message.user_id)

        if current is None:
            agent_name = self.default_agent
            agent = self.agents.get(agent_name)
            if agent is None:
                await self.channel.send_text(message.chat_id, "❌ 无可用 agent")
                return

            info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)

            # Get default model and params from config
            agent_config = self.config['agents'].get(agent_name, {})
            default_model = agent_config.get('default_model')
            default_params = agent_config.get('default_params', {}).copy()

            current = self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=agent_name,
                session_id=info.session_id,
                model=default_model,
                params=default_params,
            )

        agent = self.agents.get(current.agent_name)
        if agent is None:
            await self.channel.send_text(message.chat_id, f"❌ Agent 不存在: {current.agent_name}")
            return

        # If agent lost the session (e.g. after restart), recreate it
        if agent.get_session_info(current.session_id) is None:
            logger.info("Recovering stale session %s, creating new agent session", current.session_id)
            
            # Preserve model and params from old session
            old_model = current.model
            old_params = current.params.copy() if current.params else {}
            
            self.session_manager.destroy_session(current.session_id)
            info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
            current = self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=current.agent_name,
                session_id=info.session_id,
                model=old_model,
                params=old_params,
            )

        # Acquire per-session lock to prevent concurrent CLI invocations
        session_id = current.session_id
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        lock = self._session_locks[session_id]

        if lock.locked():
            await self.channel.send_text(
                message.chat_id,
                "⏳ 上一个请求还在处理中，请稍后再试"
            )
            return

        async with lock:
            # Clean up orphan busy state (process died without clearing flag)
            session_info = agent.get_session_info(current.session_id)
            if session_info and session_info.is_busy:
                if hasattr(agent, 'is_process_alive') and not agent.is_process_alive(current.session_id):
                    logger.warning("Session %s marked busy but process is dead, cleaning up", current.session_id)
                    if hasattr(agent, 'kill_process'):
                        await agent.kill_process(current.session_id)
                    else:
                        session_info.is_busy = False

            # Inject channel rules as context prefix for new sessions
            channel_context = self.rules_loader.get_system_prompt(message.channel)

            prompt = message.text
            if message.attachments:
                # Move attachments to session's user/ directory
                user_dir = BaseAgent.get_user_upload_dir(agent.sessions[current.session_id].work_dir)
                att_lines = []
                for att in message.attachments:
                    # Copy attachment to session workspace
                    safe_name = Path(att.filename).name  # Sanitize: prevent path traversal
                    dest = BaseAgent.safe_filename(user_dir, safe_name)
                    try:
                        shutil.copy2(att.filepath, dest)
                        att_lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
                        att_lines.append(f"  Path: {dest}")
                    except Exception as e:
                        logger.warning(f"Failed to copy attachment {att.filename}: {e}")
                        att_lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
                        att_lines.append(f"  Path: {att.filepath}")

                att_info = "\n".join(att_lines)
                if prompt:
                    prompt = f"{prompt}\n\n附件:\n{att_info}"
                else:
                    prompt = f"附件:\n{att_info}"

            # Prepend channel context to the first message of a session
            if channel_context and prompt:
                prompt = f"{channel_context}{prompt}"

            await self.channel.send_typing(message.chat_id)

            # Tell email channel which session to embed in the reply
            if message.channel == "email" and hasattr(self.channel, 'set_reply_session'):
                self.channel.set_reply_session(message.chat_id, current.session_id)

            # Collect response from agent
            use_streaming = getattr(self.channel, 'supports_streaming', True)

            buffer = ""
            # Pass model and params from session
            if use_streaming:
                # Streaming mode: progressive updates (Telegram, Discord)
                import time as _time
                message_id = None
                last_update_time = 0
                update_interval = 2.0

                async for chunk in agent.send_message(
                    current.session_id,
                    prompt,
                    model=current.model,
                    params=current.params
                ):
                    if chunk:
                        buffer += chunk
                        current_time = _time.time()
                        if current_time - last_update_time >= update_interval:
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
                # Batch mode: collect full response, send once (Email)
                async for chunk in agent.send_message(
                    current.session_id,
                    prompt,
                    model=current.model,
                    params=current.params
                ):
                    if chunk:
                        buffer += chunk

                response = buffer.strip() or "✅ 完成"
                await self.channel.send_text(message.chat_id, response)

            self.session_manager.touch(current.session_id)

            # Log prompt/response to sender's session folder (email channel only)
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
