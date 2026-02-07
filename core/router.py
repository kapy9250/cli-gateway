"""Message router for command parsing and agent forwarding."""

from __future__ import annotations

import logging
from typing import Dict

from agents.base import BaseAgent
from channels.base import BaseChannel, IncomingMessage
from core.auth import Auth
from core.session import SessionManager

logger = logging.getLogger(__name__)


class Router:
    """Route incoming messages to commands or current active session."""

    def __init__(
        self,
        auth: Auth,
        session_manager: SessionManager,
        agents: Dict[str, BaseAgent],
        channel: BaseChannel,
    ) -> None:
        self.auth = auth
        self.session_manager = session_manager
        self.agents = agents
        self.channel = channel
        self.default_agent = next(iter(agents.keys()), "claude")

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle one normalized incoming message."""
        try:
            if not self.auth.check(int(message.user_id)):
                await self.channel.send_text(message.chat_id, "⚠️ 未授权访问")
                return
        except ValueError:
            logger.warning("Invalid user id: %s", message.user_id)
            await self.channel.send_text(message.chat_id, "⚠️ 未授权访问")
            return

        text = (message.text or "").strip()
        if text.startswith("/"):
            await self._handle_command(message)
            return

        await self._forward_to_agent(message)

    async def _handle_command(self, message: IncomingMessage) -> None:
        text = (message.text or "").strip()
        parts = text.split()
        command = parts[0].split("@")[0].lower()

        if command == "/start":
            await self.channel.send_text(message.chat_id, "👋 CLI Gateway 已启动，发送 /help 查看命令。")
            return

        if command == "/help":
            await self.channel.send_text(
                message.chat_id,
                "\n".join(
                    [
                        "可用命令：",
                        "/start",
                        "/help",
                        "/agent <name>",
                        "/sessions",
                        "/kill",
                        "/current",
                        "/switch <id>",
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
            self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=agent_name,
                session_id=info.session_id,
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

        await self.channel.send_text(message.chat_id, "未知命令，发送 /help 查看支持命令。")

    async def _forward_to_agent(self, message: IncomingMessage) -> None:
        current = self.session_manager.get_active_session(message.user_id)

        if current is None:
            agent_name = self.default_agent
            agent = self.agents.get(agent_name)
            if agent is None:
                await self.channel.send_text(message.chat_id, "❌ 无可用 agent")
                return

            info = await agent.create_session(user_id=message.user_id, chat_id=message.chat_id)
            current = self.session_manager.create_session(
                user_id=message.user_id,
                chat_id=message.chat_id,
                agent_name=agent_name,
                session_id=info.session_id,
            )

        agent = self.agents.get(current.agent_name)
        if agent is None:
            await self.channel.send_text(message.chat_id, f"❌ Agent 不存在: {current.agent_name}")
            return

        prompt = message.text
        if message.attachments:
            names = ", ".join(att.filename for att in message.attachments)
            prompt = f"{prompt}\n\n[附件: {names}]" if prompt else f"[附件: {names}]"

        await self.channel.send_typing(message.chat_id)

        chunks = []
        async for chunk in agent.send_message(current.session_id, prompt):
            if chunk:
                chunks.append(chunk)

        response = "\n".join(chunks).strip() or "✅ 完成"
        self.session_manager.touch(current.session_id)
        await self.channel.send_text(message.chat_id, response)
