"""
Authentication module - whitelist-based user authorization
"""
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import List, Set, Optional, Deque, Dict

logger = logging.getLogger(__name__)


class Auth:
    """Whitelist-based authentication with optional chat allowlist"""
    
    def __init__(self, allowed_users: List[int], allowed_chats: Optional[List[int]] = None, max_requests_per_minute: int = 0, state_file: Optional[str] = None, admin_users: Optional[List[int]] = None):
        """
        Initialize auth with allowed user IDs and optional chat IDs
        
        Args:
            allowed_users: List of Telegram user IDs that are authorized
            allowed_chats: Optional list of Telegram chat IDs that are authorized
            max_requests_per_minute: Per-user rate limit (0 disables)
            state_file: Optional path to persist allowlist changes
            admin_users: Optional list of admin user IDs for privileged operations
        """
        self.allowed_users: Set[int] = set(allowed_users)
        self.allowed_chats: Optional[Set[int]] = set(allowed_chats) if allowed_chats else None
        self.max_requests_per_minute = max_requests_per_minute
        self.state_file = Path(state_file) if state_file else None
        self.admin_users: Set[int] = set(admin_users or [])
        self._request_log: Dict[int, Deque[float]] = defaultdict(deque)
        self._load_state()
        logger.info(
            "Auth initialized with %d allowed users%s",
            len(self.allowed_users),
            f", {len(self.allowed_chats)} allowed chats" if self.allowed_chats is not None else ""
        )
    
    def check(self, user_id: int, chat_id: Optional[int] = None) -> bool:
        """
        Check if user/chat is authorized
        
        Args:
            user_id: Telegram user ID to check
            chat_id: Telegram chat ID to check (optional)
            
        Returns:
            True if authorized, False otherwise
        """
        user_allowed = user_id in self.allowed_users
        chat_allowed = True if self.allowed_chats is None else (chat_id in self.allowed_chats)
        is_allowed = user_allowed and chat_allowed

        if is_allowed and self.max_requests_per_minute > 0:
            now = time.time()
            window_start = now - 60
            history = self._request_log[user_id]
            while history and history[0] < window_start:
                history.popleft()

            if len(history) >= self.max_requests_per_minute:
                logger.warning(
                    "Rate limit exceeded for user_id=%s chat_id=%s limit=%s/min",
                    user_id,
                    chat_id,
                    self.max_requests_per_minute
                )
                return False

            history.append(now)
        
        if not is_allowed:
            logger.warning(
                "Unauthorized access attempt from user_id=%s chat_id=%s (user_allowed=%s chat_allowed=%s)",
                user_id,
                chat_id,
                user_allowed,
                chat_allowed
            )
        
        return is_allowed

    def _load_state(self) -> None:
        """Load persisted auth state if available"""
        if not self.state_file or not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            users = data.get("allowed_users")
            chats = data.get("allowed_chats")
            admins = data.get("admin_users")
            if isinstance(users, list):
                self.allowed_users = set(int(u) for u in users)
            if isinstance(chats, list):
                self.allowed_chats = set(int(c) for c in chats)
            if isinstance(admins, list):
                self.admin_users = set(int(a) for a in admins)
            logger.info("Loaded persisted auth state from %s", self.state_file)
        except Exception as e:
            logger.warning("Failed to load auth state from %s: %s", self.state_file, e)

    def _save_state(self) -> None:
        """Persist auth state"""
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "allowed_users": sorted(self.allowed_users),
                "allowed_chats": sorted(self.allowed_chats) if self.allowed_chats is not None else None,
                "admin_users": sorted(self.admin_users)
            }
            self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to persist auth state to %s: %s", self.state_file, e)
    
    def add_user(self, user_id: int) -> None:
        """Add a user to the whitelist"""
        self.allowed_users.add(user_id)
        self._save_state()
        logger.info(f"Added user {user_id} to whitelist")
    
    def remove_user(self, user_id: int) -> None:
        """Remove a user from the whitelist"""
        self.allowed_users.discard(user_id)
        self.admin_users.discard(user_id)
        self._save_state()
        logger.info(f"Removed user {user_id} from whitelist")

    def is_admin(self, user_id: int) -> bool:
        """Check if user has admin role"""
        return user_id in self.admin_users

    def add_admin(self, user_id: int) -> None:
        """Grant admin role to an existing allowed user"""
        if user_id not in self.allowed_users:
            self.allowed_users.add(user_id)
        self.admin_users.add(user_id)
        self._save_state()
        logger.info(f"Granted admin role to user {user_id}")

    def remove_admin(self, user_id: int) -> None:
        """Revoke admin role"""
        self.admin_users.discard(user_id)
        self._save_state()
        logger.info(f"Revoked admin role from user {user_id}")
