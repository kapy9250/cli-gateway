"""
Telegram channel implementation
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Callable, Awaitable, List

from telegram import Update, Document, PhotoSize
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

from channels.base import BaseChannel, IncomingMessage, Attachment
from core.formatter import OutputFormatter

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    """Telegram Bot implementation"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        
        self.token = config['token']
        self.parse_mode = ParseMode.HTML if config.get('parse_mode') == 'HTML' else ParseMode.MARKDOWN_V2
        self.max_length = config.get('max_message_length', 4096)
        
        self.app: Optional[Application] = None
        self.formatter = OutputFormatter(config)
        
        logger.info("TelegramChannel initialized")
    
    async def start(self):
        """Start Telegram bot"""
        self.app = Application.builder().token(self.token).build()
        
        # Register handlers for all message types
        self.app.add_handler(MessageHandler(
            filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.ATTACHMENT,
            self._on_message
        ))
        
        # Start polling
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
        logger.info("Telegram bot started")
    
    async def stop(self):
        """Stop bot gracefully"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram bot stopped")
    
    async def send_text(self, chat_id: str, text: str) -> Optional[int]:
        """Send text message with automatic pagination. Returns message_id of first chunk."""
        if not self.app:
            logger.error("Cannot send message: bot not started")
            return None
        
        # Clean and format
        text = self.formatter.clean(text)
        
        # Split if needed
        chunks = self.formatter.split_message(text)
        
        # Send chunks
        first_message_id = None
        for chunk in chunks:
            try:
                msg = await self.app.bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=self.parse_mode
                )
                if first_message_id is None:
                    first_message_id = msg.message_id
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                # Try sending without parse mode
                try:
                    msg = await self.app.bot.send_message(
                        chat_id=int(chat_id),
                        text=chunk
                    )
                    if first_message_id is None:
                        first_message_id = msg.message_id
                except:
                    logger.error(f"Failed to send message even without parse mode")
        
        return first_message_id
    
    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        """Send file"""
        if not self.app:
            logger.error("Cannot send file: bot not started")
            return
        
        try:
            with open(filepath, 'rb') as f:
                await self.app.bot.send_document(
                    chat_id=int(chat_id),
                    document=f,
                    caption=caption if caption else None,
                    parse_mode=self.parse_mode if caption else None
                )
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
    
    async def send_typing(self, chat_id: str):
        """Send typing indicator"""
        if not self.app:
            return
        
        try:
            await self.app.bot.send_chat_action(
                chat_id=int(chat_id),
                action="typing"
            )
        except Exception as e:
            logger.error(f"Failed to send typing indicator: {e}")
    
    async def edit_message(self, chat_id: str, message_id: int, text: str):
        """Edit an existing message"""
        if not self.app:
            logger.error("Cannot edit message: bot not started")
            return
        
        # Clean and format
        text = self.formatter.clean(text)
        
        # Telegram has a 4096 character limit for edits
        if len(text) > self.max_length:
            text = text[:self.max_length - 20] + "\n\n[输出过长，已截断]"
        
        try:
            await self.app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=message_id,
                text=text,
                parse_mode=self.parse_mode
            )
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
            # If edit fails, try without parse mode
            try:
                await self.app.bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=message_id,
                    text=text
                )
            except:
                logger.error(f"Failed to edit message even without parse mode")
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming message"""
        if not update.message:
            return
        
        # Extract text (may be None for media-only messages)
        text = update.message.text or update.message.caption or ""
        
        # Process attachments
        attachments = await self._process_attachments(update)
        
        # Build IncomingMessage
        msg = IncomingMessage(
            channel="telegram",
            chat_id=str(update.effective_chat.id),
            user_id=str(update.effective_user.id),
            text=text,
            is_private=update.effective_chat.type == "private",
            is_reply_to_bot=False,  # TODO: check if replying to bot
            is_mention_bot=False,   # TODO: check for @mention
            reply_to_text=None,
            attachments=attachments
        )
        
        # Group chat filtering
        if not msg.is_private:
            # In groups, only respond to:
            # 1. Replies to bot
            # 2. Messages mentioning bot
            # 3. Commands directed to bot
            # For Phase 1, we'll just handle private chats
            logger.debug(f"Ignoring group message (Phase 1)")
            return
        
        # Forward to handler
        if self._message_handler:
            try:
                await self._message_handler(msg)
            except Exception as e:
                logger.error(f"Message handler error: {e}", exc_info=True)
                await self.send_text(msg.chat_id, f"❌ 内部错误: {str(e)}")
    
    async def _process_attachments(self, update: Update) -> List[Attachment]:
        """Download and process attachments from message"""
        attachments = []
        message = update.message
        
        # Create temp directory for this message
        temp_dir = Path(tempfile.gettempdir()) / "cli-gateway" / str(message.chat_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Handle photos
            if message.photo:
                # Get largest photo
                photo = message.photo[-1]
                file = await self.app.bot.get_file(photo.file_id)
                filepath = temp_dir / f"photo_{photo.file_id}.jpg"
                await file.download_to_drive(filepath)
                
                attachments.append(Attachment(
                    filename=f"photo_{photo.file_id}.jpg",
                    filepath=str(filepath),
                    mime_type="image/jpeg",
                    size_bytes=photo.file_size or 0
                ))
                logger.info(f"Downloaded photo: {filepath}")
            
            # Handle documents
            if message.document:
                doc: Document = message.document
                file = await self.app.bot.get_file(doc.file_id)
                filepath = temp_dir / (doc.file_name or f"file_{doc.file_id}")
                await file.download_to_drive(filepath)
                
                attachments.append(Attachment(
                    filename=doc.file_name or f"file_{doc.file_id}",
                    filepath=str(filepath),
                    mime_type=doc.mime_type or "application/octet-stream",
                    size_bytes=doc.file_size or 0
                ))
                logger.info(f"Downloaded document: {filepath}")
            
            # Handle video
            if message.video:
                video = message.video
                file = await self.app.bot.get_file(video.file_id)
                filepath = temp_dir / f"video_{video.file_id}.mp4"
                await file.download_to_drive(filepath)
                
                attachments.append(Attachment(
                    filename=f"video_{video.file_id}.mp4",
                    filepath=str(filepath),
                    mime_type=video.mime_type or "video/mp4",
                    size_bytes=video.file_size or 0
                ))
                logger.info(f"Downloaded video: {filepath}")
            
            # Handle audio
            if message.audio:
                audio = message.audio
                file = await self.app.bot.get_file(audio.file_id)
                filename = audio.file_name or f"audio_{audio.file_id}.mp3"
                filepath = temp_dir / filename
                await file.download_to_drive(filepath)
                
                attachments.append(Attachment(
                    filename=filename,
                    filepath=str(filepath),
                    mime_type=audio.mime_type or "audio/mpeg",
                    size_bytes=audio.file_size or 0
                ))
                logger.info(f"Downloaded audio: {filepath}")
        
        except Exception as e:
            logger.error(f"Failed to download attachment: {e}", exc_info=True)
        
        return attachments
