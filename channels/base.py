"""
Base classes for message channels
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, BinaryIO, Callable, Awaitable, List


@dataclass
class Attachment:
    """File attachment"""
    filename: str
    data: BinaryIO
    mime_type: str


@dataclass
class IncomingMessage:
    """Unified message format from any channel"""
    channel: str  # "telegram" | "discord" | "email"
    chat_id: str  # Channel-specific chat identifier
    user_id: str  # User unique identifier
    text: str  # Message text
    is_private: bool  # Is this a private chat?
    is_reply_to_bot: bool  # Is this replying to bot's message?
    is_mention_bot: bool  # Does this mention the bot?
    reply_to_text: Optional[str] = None  # Original message being replied to
    attachments: List[Attachment] = None  # File attachments
    
    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


class BaseChannel(ABC):
    """Abstract base class for message channels"""
    
    def __init__(self, config: dict):
        self.config = config
        self._message_handler: Optional[Callable[[IncomingMessage], Awaitable[None]]] = None
    
    @abstractmethod
    async def start(self):
        """Start listening for messages"""
        pass
    
    @abstractmethod
    async def stop(self):
        """Gracefully stop the channel"""
        pass
    
    @abstractmethod
    async def send_text(self, chat_id: str, text: str):
        """Send text message, automatically handling pagination"""
        pass
    
    @abstractmethod
    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        """Send file attachment"""
        pass
    
    @abstractmethod
    async def send_typing(self, chat_id: str):
        """Send 'typing...' status"""
        pass
    
    @abstractmethod
    async def edit_message(self, chat_id: str, message_id: int, text: str):
        """Edit an existing message"""
        pass
    
    def set_message_handler(self, handler: Callable[[IncomingMessage], Awaitable[None]]):
        """Register message callback"""
        self._message_handler = handler
