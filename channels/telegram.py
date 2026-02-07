"""
Telegram channel implementation
"""
import logging
from typing import Optional, Callable, Awaitable

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

from channels.base import BaseChannel, IncomingMessage
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
        
        # Register handlers
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self._on_message
        ))
        
        self.app.add_handler(MessageHandler(
            filters.COMMAND,
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
    
    async def send_text(self, chat_id: str, text: str):
        """Send text message with automatic pagination"""
        if not self.app:
            logger.error("Cannot send message: bot not started")
            return
        
        # Clean and format
        text = self.formatter.clean(text)
        
        # Split if needed
        chunks = self.formatter.split_message(text)
        
        # Send chunks
        for chunk in chunks:
            try:
                await self.app.bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=self.parse_mode
                )
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                # Try sending without parse mode
                try:
                    await self.app.bot.send_message(
                        chat_id=int(chat_id),
                        text=chunk
                    )
                except:
                    logger.error(f"Failed to send message even without parse mode")
    
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
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming message"""
        if not update.message or not update.message.text:
            return
        
        # Build IncomingMessage
        msg = IncomingMessage(
            channel="telegram",
            chat_id=str(update.effective_chat.id),
            user_id=str(update.effective_user.id),
            text=update.message.text,
            is_private=update.effective_chat.type == "private",
            is_reply_to_bot=False,  # TODO: check if replying to bot
            is_mention_bot=False,   # TODO: check for @mention
            reply_to_text=None,
            attachments=[]
        )
        
        # Group chat filtering
        if not msg.is_private:
            # In groups, only respond to:
            # 1. Replies to bot
            # 2. Messages mentioning bot
            # 3. Commands directed to bot
            # For Phase 1, we'll just handle private chats
            logger.debug(f"Ignoring group message (Phase 1): {msg.text[:50]}")
            return
        
        # Forward to handler
        if self._message_handler:
            try:
                await self._message_handler(msg)
            except Exception as e:
                logger.error(f"Message handler error: {e}", exc_info=True)
                await self.send_text(msg.chat_id, f"❌ 内部错误: {str(e)}")
