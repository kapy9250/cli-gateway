"""
Authentication module - per-channel whitelist-based user authorization
"""
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import List, Set, Optional, Deque, Dict

logger = logging.getLogger(__name__)


class Auth:
    """Per-channel whitelist-based authentication with rate limiting.

    Each channel (telegram, discord, email) maintains its own allowed_users set.
    User identifiers are stored as strings to support both numeric IDs (Telegram/Discord)
    and email addresses (Email channel).
    """

    def __init__(
        self,
        channel_allowed: Optional[Dict[str, List[str]]] = None,
        max_requests_per_minute: int = 0,
        state_file: Optional[str] = None,
        admin_users: Optional[List[str]] = None,
    ):
        """
        Args:
            channel_allowed: Mapping of channel name -> list of allowed user identifiers.
                             e.g. {"telegram": ["286194552"], "email": ["a@b.com"]}
            max_requests_per_minute: Per-user rate limit (0 disables)
            state_file: Optional path to persist allowlist changes
            admin_users: Optional list of admin user IDs for privileged operations
        """
        # Per-channel allowed sets (values are strings)
        self._channel_allowed: Dict[str, Set[str]] = {}
        for ch, users in (channel_allowed or {}).items():
            self._channel_allowed[ch] = set(str(u) for u in users)

        self.max_requests_per_minute = max_requests_per_minute
        self.state_file = Path(state_file) if state_file else None
        self.admin_users: Set[str] = set(str(a) for a in (admin_users or []))
        self._request_log: Dict[str, Deque[float]] = defaultdict(deque)
        self._load_state()

        total = sum(len(v) for v in self._channel_allowed.values())
        logger.info(
            "Auth initialized: %d channel(s), %d total allowed entries",
            len(self._channel_allowed),
            total,
        )

    # ── backwards-compat property ──

    @property
    def allowed_users(self) -> Set[str]:
        """Union of all channel allowed users (for display / startup banner)."""
        result: Set[str] = set()
        for users in self._channel_allowed.values():
            result.update(users)
        return result

    # ── core check ──

    def check(self, user_id: str, channel: Optional[str] = None) -> bool:
        """
        Check if user is authorized on the given channel.

        Args:
            user_id: User identifier (numeric string or email)
            channel: Channel name ("telegram", "discord", "email").
                     If None, checks if user is allowed on *any* channel.
        """
        user_id = str(user_id)

        if channel:
            allowed_set = self._channel_allowed.get(channel)
            if allowed_set is None:
                # Channel has no allowlist configured -> deny
                logger.warning("No allowed_users configured for channel '%s'", channel)
                is_allowed = False
            else:
                is_allowed = user_id in allowed_set
        else:
            # Fallback: allowed on any channel
            is_allowed = any(user_id in s for s in self._channel_allowed.values())

        if is_allowed and self.max_requests_per_minute > 0:
            now = time.time()
            window_start = now - 60
            history = self._request_log[user_id]
            while history and history[0] < window_start:
                history.popleft()

            if len(history) >= self.max_requests_per_minute:
                logger.warning(
                    "Rate limit exceeded for user_id=%s channel=%s limit=%s/min",
                    user_id, channel, self.max_requests_per_minute,
                )
                return False

            history.append(now)

        if not is_allowed:
            logger.warning(
                "Unauthorized access: user_id=%s channel=%s",
                user_id, channel,
            )

        return is_allowed

    # ── mutation helpers ──

    def add_user(self, user_id: str, channel: str) -> None:
        """Add a user to a channel's whitelist."""
        user_id = str(user_id)
        if channel not in self._channel_allowed:
            self._channel_allowed[channel] = set()
        self._channel_allowed[channel].add(user_id)
        self._save_state()
        logger.info("Added user %s to channel %s whitelist", user_id, channel)

    def remove_user(self, user_id: str, channel: str) -> None:
        """Remove a user from a channel's whitelist."""
        user_id = str(user_id)
        allowed = self._channel_allowed.get(channel)
        if allowed:
            allowed.discard(user_id)
        self.admin_users.discard(user_id)
        self._save_state()
        logger.info("Removed user %s from channel %s whitelist", user_id, channel)

    def get_channel_users(self, channel: str) -> Set[str]:
        """Get allowed users for a specific channel."""
        return self._channel_allowed.get(channel, set()).copy()

    # ── admin helpers ──

    def is_admin(self, user_id) -> bool:
        return str(user_id) in self.admin_users

    def add_admin(self, user_id: str) -> None:
        user_id = str(user_id)
        self.admin_users.add(user_id)
        self._save_state()
        logger.info("Granted admin role to user %s", user_id)

    def remove_admin(self, user_id: str) -> None:
        self.admin_users.discard(str(user_id))
        self._save_state()
        logger.info("Revoked admin role from user %s", user_id)

    # ── persistence ──

    def _load_state(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))

            # New format: per-channel
            ch = data.get("channel_allowed")
            if isinstance(ch, dict):
                for channel, users in ch.items():
                    if isinstance(users, list):
                        self._channel_allowed[channel] = set(str(u) for u in users)

            # Legacy format: flat allowed_users (treat as telegram)
            elif isinstance(data.get("allowed_users"), list):
                self._channel_allowed["telegram"] = set(
                    str(u) for u in data["allowed_users"]
                )

            admins = data.get("admin_users")
            if isinstance(admins, list):
                self.admin_users = set(str(a) for a in admins)

            logger.info("Loaded persisted auth state from %s", self.state_file)
        except Exception as e:
            logger.warning("Failed to load auth state from %s: %s", self.state_file, e)

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "channel_allowed": {
                    ch: sorted(users) for ch, users in self._channel_allowed.items()
                },
                "admin_users": sorted(self.admin_users),
            }
            self.state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to persist auth state to %s: %s", self.state_file, e)
