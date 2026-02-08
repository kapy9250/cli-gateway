"""
Email channel implementation - IMAP polling + SMTP sending
"""
import asyncio
import email
import email.header
import email.utils
import imaplib
import logging
import os
import smtplib
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional, Callable, Awaitable, List, Set

from channels.base import BaseChannel, IncomingMessage, Attachment

logger = logging.getLogger(__name__)


class EmailChannel(BaseChannel):
    """Email channel via IMAP (receive) + SMTP (send)"""

    def __init__(self, config: dict):
        super().__init__(config)

        # IMAP config
        self.imap_host = config['imap_host']
        self.imap_port = config.get('imap_port', 993)
        self.imap_use_ssl = config.get('imap_ssl', True)

        # SMTP config
        self.smtp_host = config['smtp_host']
        self.smtp_port = config.get('smtp_port', 587)
        self.smtp_use_tls = config.get('smtp_tls', True)

        # Credentials
        self.username = config['username']
        self.password = config['password']

        # Polling
        self.poll_interval = config.get('poll_interval', 30)

        # Auth
        self.allowed_senders: Set[str] = set(
            s.lower() for s in config.get('allowed_senders', [])
        )

        # State
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._seen_uids: Set[str] = set()
        # Map chat_id (sender email) -> last message-id for threading
        self._thread_refs: dict = {}

        logger.info("EmailChannel initialized")

    async def start(self):
        """Start IMAP polling loop"""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Email channel started (polling every %ds)", self.poll_interval)

    async def stop(self):
        """Stop polling"""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("Email channel stopped")

    async def send_text(self, chat_id: str, text: str) -> Optional[int]:
        """
        Send email reply.
        chat_id = recipient email address
        """
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.username
            msg['To'] = chat_id
            msg['Subject'] = "Re: CLI Gateway"

            # Add threading headers if available
            refs = self._thread_refs.get(chat_id)
            if refs:
                msg['In-Reply-To'] = refs.get('message_id', '')
                msg['References'] = refs.get('references', '')

            # Plain text part
            msg.attach(MIMEText(text, 'plain', 'utf-8'))

            # HTML part (basic formatting)
            html_text = text.replace('\n', '<br>\n')
            msg.attach(MIMEText(f"<html><body>{html_text}</body></html>", 'html', 'utf-8'))

            await asyncio.to_thread(self._smtp_send, chat_id, msg)
            logger.info(f"Email sent to {chat_id}")
            return None  # Email doesn't have numeric message IDs

        except Exception as e:
            logger.error(f"Failed to send email to {chat_id}: {e}", exc_info=True)
            return None

    async def send_file(self, chat_id: str, filepath: str, caption: str = ""):
        """Send email with file attachment"""
        try:
            msg = MIMEMultipart()
            msg['From'] = self.username
            msg['To'] = chat_id
            msg['Subject'] = "Re: CLI Gateway"

            if caption:
                msg.attach(MIMEText(caption, 'plain', 'utf-8'))

            # Attach file
            path = Path(filepath)
            with open(filepath, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{path.name}"'
            )
            msg.attach(part)

            await asyncio.to_thread(self._smtp_send, chat_id, msg)
            logger.info(f"Email with attachment sent to {chat_id}")

        except Exception as e:
            logger.error(f"Failed to send email with attachment: {e}", exc_info=True)

    async def send_typing(self, chat_id: str):
        """No typing indicator for email - no-op"""
        pass

    async def edit_message(self, chat_id: str, message_id: int, text: str):
        """Cannot edit emails - send a new one instead"""
        await self.send_text(chat_id, text)

    def _smtp_send(self, recipient: str, msg):
        """Synchronous SMTP send (called via asyncio.to_thread)"""
        if self.smtp_use_tls:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)

        try:
            server.login(self.username, self.password)
            server.sendmail(self.username, recipient, msg.as_string())
        finally:
            server.quit()

    async def _poll_loop(self):
        """Background IMAP polling loop"""
        while self._running:
            try:
                messages = await asyncio.to_thread(self._imap_fetch_new)
                for incoming in messages:
                    if self._message_handler:
                        try:
                            await self._message_handler(incoming)
                        except Exception as e:
                            logger.error(f"Email handler error: {e}", exc_info=True)
                            await self.send_text(
                                incoming.chat_id,
                                f"❌ 处理错误: {str(e)}"
                            )
            except Exception as e:
                logger.error(f"IMAP poll error: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    def _imap_fetch_new(self) -> List[IncomingMessage]:
        """Synchronous IMAP fetch (called via asyncio.to_thread)"""
        messages = []

        try:
            if self.imap_use_ssl:
                imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            else:
                imap = imaplib.IMAP4(self.imap_host, self.imap_port)

            imap.login(self.username, self.password)
            imap.select('INBOX')

            # Search for unseen messages
            status, data = imap.search(None, 'UNSEEN')
            if status != 'OK' or not data[0]:
                imap.logout()
                return messages

            uids = data[0].split()

            for uid in uids:
                uid_str = uid.decode()
                if uid_str in self._seen_uids:
                    continue

                status, msg_data = imap.fetch(uid, '(RFC822)')
                if status != 'OK':
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Parse sender
                from_header = msg.get('From', '')
                sender_name, sender_addr = email.utils.parseaddr(from_header)
                sender_addr = sender_addr.lower()

                # Auth check
                if self.allowed_senders and sender_addr not in self.allowed_senders:
                    logger.warning(f"Unauthorized email from: {sender_addr}")
                    self._seen_uids.add(uid_str)
                    continue

                # Parse subject
                subject = self._decode_header(msg.get('Subject', ''))

                # Parse body
                body = self._extract_body(msg)

                # Parse attachments
                attachments = self._extract_attachments(msg)

                # Store threading info
                message_id = msg.get('Message-ID', '')
                references = msg.get('References', '')
                self._thread_refs[sender_addr] = {
                    'message_id': message_id,
                    'references': f"{references} {message_id}".strip(),
                    'subject': subject,
                }

                # Build text (subject + body)
                text = body
                if subject and not subject.lower().startswith('re:'):
                    text = f"[Subject: {subject}]\n\n{body}"

                incoming = IncomingMessage(
                    channel="email",
                    chat_id=sender_addr,
                    user_id=sender_addr,
                    text=text.strip(),
                    is_private=True,
                    is_reply_to_bot=subject.lower().startswith('re:') if subject else False,
                    is_mention_bot=True,  # All emails are directed at us
                    reply_to_text=None,
                    attachments=attachments,
                )
                messages.append(incoming)
                self._seen_uids.add(uid_str)

            imap.logout()

        except Exception as e:
            logger.error(f"IMAP fetch error: {e}", exc_info=True)

        return messages

    @staticmethod
    def _decode_header(header_value: str) -> str:
        """Decode RFC2047 encoded header"""
        if not header_value:
            return ""
        decoded_parts = email.header.decode_header(header_value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or 'utf-8', errors='replace'))
            else:
                result.append(part)
        return ' '.join(result)

    @staticmethod
    def _extract_body(msg) -> str:
        """Extract plain text body from email message"""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get('Content-Disposition', ''))

                # Skip attachments
                if 'attachment' in disposition:
                    continue

                if content_type == 'text/plain':
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')

            # Fallback to HTML if no plain text
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or 'utf-8'
                    # Basic HTML stripping
                    import re
                    html = payload.decode(charset, errors='replace')
                    return re.sub(r'<[^>]+>', '', html).strip()
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')

        return ""

    def _extract_attachments(self, msg) -> List[Attachment]:
        """Extract and save attachments from email"""
        attachments = []
        temp_dir = Path(tempfile.gettempdir()) / "cli-gateway" / "email"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for part in msg.walk():
            disposition = str(part.get('Content-Disposition', ''))
            if 'attachment' not in disposition and 'inline' not in disposition:
                continue

            # Skip text parts
            if part.get_content_maintype() == 'text':
                continue

            filename = part.get_filename()
            if filename:
                filename = self._decode_header(filename)
            else:
                ext = part.get_content_type().split('/')[-1]
                filename = f"attachment.{ext}"

            # Sanitize filename
            filename = Path(filename).name

            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                filepath = temp_dir / filename
                # Avoid overwrite
                counter = 1
                while filepath.exists():
                    stem = Path(filename).stem
                    suffix = Path(filename).suffix
                    filepath = temp_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                filepath.write_bytes(payload)

                attachments.append(Attachment(
                    filename=filename,
                    filepath=str(filepath),
                    mime_type=part.get_content_type(),
                    size_bytes=len(payload),
                ))
                logger.info(f"Extracted email attachment: {filepath}")

            except Exception as e:
                logger.error(f"Failed to extract attachment {filename}: {e}")

        return attachments
