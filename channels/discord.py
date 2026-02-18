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

    supports_streaming = True

    def __init__(self, config: dict):
        super().__init__(config)

        self.token = config['token']
        self.max_length = config.get('max_message_length', 2000)
        self.stream_update_interval = max(0.0, float(config.get('stream_update_interval', 0.0)))
        self.allowed_guilds = set(config.get('allowed_guilds', []))
        self.allow_bots = config.get('allow_bots', config.get('allowBots', True))
        self.enforce_at_sender = config.get('enforce_at_sender', True)
        self._reply_target: dict = {}

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
        self._channel_cache: dict = {}  # chat_id -> channel object
        self._handler_tasks: set[asyncio.Task] = set()
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
        # Login first, then launch the websocket connection as a background task
        await self.client.login(self.token)
        self._run_task = asyncio.create_task(self.client.connect())
        # Wait until the READY event fires
        await self.client.wait_until_ready()
        logger.info("Discord bot started")

    async def stop(self):
        """Stop bot gracefully"""
        if self._handler_tasks:
            for task in list(self._handler_tasks):
                task.cancel()
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
            self._handler_tasks.clear()
        if self.client and not self.client.is_closed():
            await self.client.close()
            logger.info("Discord bot stopped")

    async def _dispatch_message(self, msg: IncomingMessage):
        """Run message handler in a background task so gateway callbacks stay responsive."""
        if not self._message_handler:
            return
        try:
            await self._message_handler(msg)
        except Exception as e:
            logger.error(f"Message handler error: {e}", exc_info=True)
            await self.send_text(msg.chat_id, f"❌ 内部错误: {str(e)}")

    async def _resolve_channel(self, chat_id: str):
        """Resolve channel by ID: local cache → client cache → API fetch."""
        cid = int(chat_id)
        ch = self._channel_cache.get(cid) or self.client.get_channel(cid)
        if ch:
            return ch
        try:
            ch = await self.client.fetch_channel(cid)
            self._channel_cache[cid] = ch
            return ch
        except Exception as e:
            logger.error(f"Failed to fetch channel {chat_id}: {e}")
            return None

    async def send_text(self, chat_id: str, text: str) -> Optional[int]:
        """Send text message with automatic pagination. Returns message_id of first chunk."""
        channel = await self._resolve_channel(chat_id)
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return None

        # Enforce @sender in guild channels for clear notification routing
        text = self._apply_required_mention(chat_id, text)

        # Normalize content for Discord markdown rendering.
        text = self.formatter.render_for_channel(text, "discord")

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

    def _apply_required_mention(self, chat_id: str, text: str) -> str:
        if not self.enforce_at_sender:
            return text
        target = self._reply_target.get(str(chat_id))
        if not target or target.get("is_private"):
            return text
        mention = target.get("mention")
        if not mention:
            return text
        if mention in text:
            return text
        return f"{mention} {text}".strip()

    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        """Send file attachment"""
        channel = await self._resolve_channel(chat_id)
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return

        try:
            file = File(filepath)
            content = self.formatter.render_for_channel(caption, "discord") if caption else None
            await channel.send(content=content, file=file)
        except Exception as e:
            logger.error(f"Failed to send Discord file: {e}")

    async def send_typing(self, chat_id: str):
        """Send typing indicator"""
        channel = await self._resolve_channel(chat_id)
        if not channel:
            return

        try:
            await channel.typing()
        except Exception as e:
            logger.error(f"Failed to send typing indicator: {e}")

    async def edit_message(self, chat_id: str, message_id: int, text: str):
        """Edit an existing message"""
        channel = await self._resolve_channel(chat_id)
        if not channel:
            logger.error(f"Channel {chat_id} not found")
            return

        text = self.formatter.render_for_channel(text, "discord")
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

        # Ignore bot-authored messages only when explicitly disabled
        if message.author.bot and not self.allow_bots:
            return

        # Cache the channel object for later send_text lookups
        self._channel_cache[message.channel.id] = message.channel

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

        # In guilds, only respond to mentions or replies
        if not is_dm and not is_mention and not is_reply_to_bot:
            return

        # Process attachments
        attachments = await self._process_attachments(message)

        # Strip bot mention from text
        text = message.content or ""
        if self.client.user:
            text = text.replace(f"<@{self.bot_id}>", "").replace(f"<@!{self.bot_id}>", "").strip()

        # Build IncomingMessage
        sender_username = message.author.name
        sender_display_name = getattr(message.author, "display_name", None) or message.author.name
        sender_mention = f"<@{message.author.id}>"

        msg = IncomingMessage(
            channel="discord",
            chat_id=str(message.channel.id),
            user_id=str(message.author.id),
            text=text,
            is_private=is_dm,
            is_reply_to_bot=is_reply_to_bot,
            is_mention_bot=is_mention,
            is_from_bot=bool(message.author.bot),
            guild_id=str(message.guild.id) if message.guild else None,
            sender_username=sender_username,
            sender_display_name=sender_display_name,
            sender_mention=sender_mention,
            reply_to_text=reply_to_text,
            attachments=attachments,
        )

        self._reply_target[msg.chat_id] = {"mention": sender_mention, "is_private": msg.is_private}

        if self._message_handler:
            task = asyncio.create_task(self._dispatch_message(msg))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)

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
