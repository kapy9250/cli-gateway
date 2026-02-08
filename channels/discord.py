"""
Discord channel implementation
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable, List

import discord
from discord import Intents, Message, File

from channels.base import BaseChannel, IncomingMessage, Attachment
from core.formatter import OutputFormatter

logger = logging.getLogger(__name__)


class DiscordChannel(BaseChannel):
    """Discord Bot implementation using discord.py"""

    def __init__(self, config: dict):
        super().__init__(config)

        self.token = config['token']
        self.max_length = config.get('max_message_length', 2000)
        self.allowed_guilds = set(config.get('allowed_guilds', []))

        # Setup intents
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = config.get('members_intent', False)

        self.client = discord.Client(intents=intents)
        self.formatter = OutputFormatter({
            **config,
            'parse_mode': 'Markdown',
            'max_message_length': self.max_length,
        })

        self.bot_id: Optional[int] = None
        self._setup_events()

        logger.info("DiscordChannel initialized")

    def _setup_events(self):
        """Register discord.py event handlers"""

        @self.client.event
        async def on_ready():
            self.bot_id = self.client.user.id
            logger.info(f"Discord bot logged in as {self.client.user} (id={self.bot_id})")

        @self.client.event
        async def on_message(message: Message):
            await self._on_message(message)

    async def start(self):
        """Start Discord bot (non-blocking)"""
        self._run_task = asyncio.create_task(self.client.start(self.token))
        # Wait until ready
        await self.client.wait_until_ready()
        logger.info("Discord bot started")

    async def stop(self):
        """Stop bot gracefully"""
        if self.client and not self.client.is_closed():
            await self.client.close()
            logger.info("Discord bot stopped")

    async def send_text(self, chat_id: str, text: str) -> Optional[int]:
        """Send text message with automatic pagination. Returns message_id of first chunk."""
        channel = self.client.get_channel(int(chat_id))
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return None

        # Clean and format
        text = self.formatter.clean(text)

        # Split if needed
        chunks = self.formatter.split_message(text)

        first_message_id = None
        for chunk in chunks:
            try:
                msg = await channel.send(chunk)
                if first_message_id is None:
                    first_message_id = msg.id
            except Exception as e:
                logger.error(f"Failed to send Discord message: {e}")

        return first_message_id

    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        """Send file attachment"""
        channel = self.client.get_channel(int(chat_id))
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return

        try:
            file = File(filepath)
            await channel.send(content=caption or None, file=file)
        except Exception as e:
            logger.error(f"Failed to send Discord file: {e}")

    async def send_typing(self, chat_id: str):
        """Send typing indicator"""
        channel = self.client.get_channel(int(chat_id))
        if not channel:
            return

        try:
            await channel.typing()
        except Exception as e:
            logger.error(f"Failed to send typing indicator: {e}")

    async def edit_message(self, chat_id: str, message_id: int, text: str):
        """Edit an existing message"""
        channel = self.client.get_channel(int(chat_id))
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return

        text = self.formatter.clean(text)
        if len(text) > self.max_length:
            text = text[:self.max_length - 20] + "\n\n[输出过长，已截断]"

        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(content=text)
        except Exception as e:
            logger.error(f"Failed to edit Discord message: {e}")

    async def _on_message(self, message: Message):
        """Handle incoming Discord message"""
        # Ignore own messages
        if message.author.id == self.bot_id:
            return

        # Ignore bots (optional, configurable)
        if message.author.bot:
            return

        # Guild filtering
        if self.allowed_guilds and message.guild:
            if message.guild.id not in self.allowed_guilds:
                return

        # Determine if bot is mentioned or replied to
        is_mention = self.client.user in message.mentions if self.client.user else False
        is_reply_to_bot = False
        reply_to_text = None

        if message.reference and message.reference.resolved:
            ref_msg = message.reference.resolved
            if hasattr(ref_msg, 'author') and ref_msg.author.id == self.bot_id:
                is_reply_to_bot = True
            if hasattr(ref_msg, 'content'):
                reply_to_text = ref_msg.content

        is_dm = message.guild is None

        # In guilds, only respond to mentions, replies, or commands
        if not is_dm and not is_mention and not is_reply_to_bot:
            text = message.content or ""
            if not text.startswith("/") and not text.lower().startswith("kapybara "):
                return

        # Process attachments
        attachments = await self._process_attachments(message)

        # Strip bot mention from text
        text = message.content or ""
        if self.client.user:
            text = text.replace(f"<@{self.bot_id}>", "").replace(f"<@!{self.bot_id}>", "").strip()

        # Build IncomingMessage
        msg = IncomingMessage(
            channel="discord",
            chat_id=str(message.channel.id),
            user_id=str(message.author.id),
            text=text,
            is_private=is_dm,
            is_reply_to_bot=is_reply_to_bot,
            is_mention_bot=is_mention,
            reply_to_text=reply_to_text,
            attachments=attachments,
        )

        if self._message_handler:
            try:
                await self._message_handler(msg)
            except Exception as e:
                logger.error(f"Message handler error: {e}", exc_info=True)
                await self.send_text(msg.chat_id, f"❌ 内部错误: {str(e)}")

    async def _process_attachments(self, message: Message) -> List[Attachment]:
        """Download and process attachments from Discord message"""
        attachments = []
        import tempfile

        temp_dir = Path(tempfile.gettempdir()) / "cli-gateway" / str(message.channel.id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        for att in message.attachments:
            try:
                filepath = temp_dir / att.filename
                await att.save(filepath)

                attachments.append(Attachment(
                    filename=att.filename,
                    filepath=str(filepath),
                    mime_type=att.content_type or "application/octet-stream",
                    size_bytes=att.size,
                ))
                logger.info(f"Downloaded Discord attachment: {filepath}")
            except Exception as e:
                logger.error(f"Failed to download attachment {att.filename}: {e}")

        return attachments
