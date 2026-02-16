"""
Email channel implementation - IMAP polling + SMTP sending

Fixes over original:
- Uses IMAP UID commands instead of sequence numbers (stable across sessions)
- Pre-filters sender via BODY.PEEK[HEADER] before downloading full body
- Non-whitelisted emails stay UNSEEN on IMAP server
- Per-sender folder structure for emails, attachments, and session logs
"""
import asyncio
import email
import email.header
import email.utils
import html
import imaplib
import json
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional, List, Set

from channels.base import BaseChannel, IncomingMessage, Attachment
from utils.constants import SESSION_MARKER_TEMPLATE, SESSION_MARKER_RE

logger = logging.getLogger(__name__)


def _sanitize_dirname(addr: str) -> str:
    """Turn an email address into a safe directory name.

    e.g. 'Click.Song@gmail.com' -> 'click.song_at_gmail.com'
    """
    return addr.lower().replace("@", "_at_")


class EmailChannel(BaseChannel):
    """Email channel via IMAP (receive) + SMTP (send)"""

    # Indicate that email does NOT support real-time message editing.
    # The Router should collect the full response before calling send_text.
    supports_streaming = False

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

        # Data directory for per-sender storage
        self.data_dir = Path(config.get('data_dir', './data/email'))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Pre-filter: skip unauthorized senders at IMAP level to avoid sending
        # reply emails to spam. The Router also does auth.check() but that would
        # trigger a reply email which is undesirable for unknown senders.
        self._allowed_senders: Set[str] = set(
            str(s).lower() for s in config.get('allowed_users', [])
        )

        # State
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._seen_uids: Set[str] = set()
        # Map chat_id (sender email) -> threading info + original body
        self._thread_refs: dict = {}
        # Map chat_id -> session_id for including in the next reply
        self._reply_session_id: dict = {}

        logger.info("EmailChannel initialized (data_dir=%s)", self.data_dir)

    # ── per-sender directory helpers ──

    def _sender_dir(self, sender_addr: str) -> Path:
        """Get (and create) the root directory for a sender."""
        d = self.data_dir / _sanitize_dirname(sender_addr)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sender_emails_dir(self, sender_addr: str) -> Path:
        d = self._sender_dir(sender_addr) / "emails"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sender_attachments_dir(self, sender_addr: str) -> Path:
        d = self._sender_dir(sender_addr) / "attachments"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sender_sessions_dir(self, sender_addr: str) -> Path:
        d = self._sender_dir(sender_addr) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_email_record(self, sender_addr: str, subject: str, body: str,
                           message_id: str, attachments: List[Attachment]) -> None:
        """Persist an incoming email to the sender's emails/ folder."""
        emails_dir = self._sender_emails_dir(sender_addr)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from": sender_addr,
            "subject": subject,
            "message_id": message_id,
            "body": body,
            "attachments": [
                {"filename": a.filename, "path": a.filepath,
                 "mime_type": a.mime_type, "size_bytes": a.size_bytes}
                for a in attachments
            ],
        }
        filepath = emails_dir / f"{ts}.json"
        # Avoid collision
        counter = 1
        while filepath.exists():
            filepath = emails_dir / f"{ts}_{counter}.json"
            counter += 1
        filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Saved email record: %s", filepath)

    def set_reply_session(self, chat_id: str, session_id: str) -> None:
        """Set session ID to embed in the next reply to this chat."""
        self._reply_session_id[chat_id] = session_id

    @staticmethod
    def _extract_session_hint(body: str) -> Optional[str]:
        """Extract session ID from email body (including quoted reply text).

        Looks for the HTML-comment marker ``<!-- clawdbot-session:<id> -->``
        that we embed in outgoing replies.  Returns the first match, which
        corresponds to the most recent reply in the thread.
        """
        m = SESSION_MARKER_RE.search(body)
        return m.group(1) if m else None

    def save_session_log(self, sender_addr: str, session_id: str,
                         prompt: str, response: str) -> None:
        """Append a prompt/response exchange to the sender's session log."""
        sessions_dir = self._sender_sessions_dir(sender_addr)
        log_file = sessions_dir / f"{session_id}.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "response": response,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── channel interface ──

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
        Send email reply with quoted original.
        chat_id = recipient email address
        """
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.username
            msg['To'] = chat_id

            # Use original subject for threading
            refs = self._thread_refs.get(chat_id)
            if refs and refs.get('subject'):
                subject = refs['subject']
                if not subject.lower().startswith('re:'):
                    subject = f"Re: {subject}"
                msg['Subject'] = subject
            else:
                msg['Subject'] = "Re: CLI Gateway"

            # Add threading headers
            if refs:
                msg['In-Reply-To'] = refs.get('message_id', '')
                msg['References'] = refs.get('references', '')

            # Append session marker so the recipient can reply to continue.
            # Uses an HTML comment — invisible in rendered email, impossible to
            # accidentally type, and survives quoted-reply chains.
            session_id = self._reply_session_id.pop(chat_id, None)
            if session_id:
                marker = SESSION_MARKER_TEMPLATE.format(session_id=session_id)
                text += f"\n\n{marker}"

            # Build body with quoted original
            original_body = refs.get('original_body', '') if refs else ''
            plain_body = text
            html_body = html.escape(text).replace('\n', '<br>\n')

            if original_body:
                # Plain text: quote with >
                quoted_lines = '\n'.join(
                    f'> {line}' for line in original_body.split('\n')
                )
                plain_body = f"{text}\n\n---\n{quoted_lines}"

                # HTML: styled blockquote
                quoted_html = html.escape(original_body).replace('\n', '<br>\n')
                html_body = (
                    f"{html.escape(text).replace(chr(10), '<br>' + chr(10))}"
                    f"<br><br><hr>"
                    f'<blockquote style="margin:10px 0;padding:10px;'
                    f'border-left:3px solid #ccc;color:#555">'
                    f"{quoted_html}</blockquote>"
                )

            msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))
            msg.attach(MIMEText(
                f"<html><body>{html_body}</body></html>", 'html', 'utf-8'
            ))

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
        """Email cannot edit - no-op (Router should use batch mode)"""
        pass

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
        """Synchronous IMAP fetch using UID commands.

        1. UID SEARCH UNSEEN  — get stable UIDs
        2. UID FETCH (BODY.PEEK[HEADER])  — check sender without marking read
        3. Skip non-whitelisted senders (they stay UNSEEN)
        4. UID FETCH (RFC822)  — download full body for whitelisted senders
           (this implicitly sets \\Seen flag)
        """
        messages = []

        try:
            if self.imap_use_ssl:
                imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            else:
                imap = imaplib.IMAP4(self.imap_host, self.imap_port)

            imap.login(self.username, self.password)
            imap.select('INBOX')

            # UID SEARCH for unseen messages (UIDs are stable across sessions)
            status, data = imap.uid('search', None, 'UNSEEN')  # type: ignore[arg-type]
            if status != 'OK' or not data[0]:
                imap.logout()
                return messages

            uid_list = data[0].split()

            for uid in uid_list:
                uid_str = uid.decode()
                if uid_str in self._seen_uids:
                    continue

                # Step 1: Peek at headers only (does NOT set \Seen flag)
                status, header_data = imap.uid('fetch', uid, '(BODY.PEEK[HEADER])')
                if status != 'OK':
                    continue

                raw_header = header_data[0][1]
                header_msg = email.message_from_bytes(raw_header)

                # Parse sender from header
                from_header = header_msg.get('From', '')
                sender_name, sender_addr = email.utils.parseaddr(from_header)
                sender_addr = sender_addr.lower()

                # Pre-filter: skip unauthorized senders silently.
                # Because we used BODY.PEEK, the message stays UNSEEN.
                if self._allowed_senders and sender_addr not in self._allowed_senders:
                    logger.info("Skipping email from non-whitelisted sender: %s (stays unread)", sender_addr)
                    self._seen_uids.add(uid_str)
                    continue

                # Step 2: Fetch full message for whitelisted senders.
                # RFC822 (= BODY[]) implicitly sets the \Seen flag.
                status, msg_data = imap.uid('fetch', uid, '(RFC822)')
                if status != 'OK':
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Parse subject
                subject = self._decode_header(msg.get('Subject', ''))

                # Parse body (full text including quoted portions)
                body = self._extract_body(msg)

                # Extract session hint from body (may appear in quoted reply)
                session_hint = self._extract_session_hint(body)

                # Parse and save attachments to sender's folder
                attachments = self._extract_attachments(msg, sender_addr)

                # Store threading info + original body for quoting
                message_id = msg.get('Message-ID', '')
                references = msg.get('References', '')
                self._thread_refs[sender_addr] = {
                    'message_id': message_id,
                    'references': f"{references} {message_id}".strip(),
                    'subject': subject,
                    'original_body': body,
                }

                # Save email record to sender's emails/ folder
                self._save_email_record(
                    sender_addr, subject, body, message_id, attachments
                )

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
                    session_hint=session_hint,
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
                    text = payload.decode(charset, errors='replace')
                    return re.sub(r'<[^>]+>', '', text).strip()
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')

        return ""

    def _extract_attachments(self, msg, sender_addr: str) -> List[Attachment]:
        """Extract and save attachments to the sender's attachments/ folder."""
        attachments = []
        att_dir = self._sender_attachments_dir(sender_addr)

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

                # Add timestamp prefix to avoid collisions
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                stem = Path(filename).stem
                suffix = Path(filename).suffix
                dest_name = f"{ts}_{stem}{suffix}"
                filepath = att_dir / dest_name

                # Extra safety: avoid overwrite
                counter = 1
                while filepath.exists():
                    filepath = att_dir / f"{ts}_{stem}_{counter}{suffix}"
                    counter += 1

                filepath.write_bytes(payload)

                attachments.append(Attachment(
                    filename=filename,
                    filepath=str(filepath),
                    mime_type=part.get_content_type(),
                    size_bytes=len(payload),
                ))
                logger.info(f"Saved email attachment: {filepath}")

            except Exception as e:
                logger.error(f"Failed to extract attachment {filename}: {e}")

        return attachments
