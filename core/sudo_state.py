"""Per-chat sudo state window for system-mode gateway."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class SudoWindow:
    user_id: str
    channel: str
    chat_id: str
    created_at: float
    expires_at: float


class SudoStateManager:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._windows: Dict[str, SudoWindow] = {}

    @staticmethod
    def _key(user_id: str, channel: str, chat_id: str) -> str:
        return f"{str(user_id)}|{str(channel)}|{str(chat_id)}"

    def _cleanup(self, now: Optional[float] = None) -> None:
        ts = time.time() if now is None else float(now)
        stale = [k for k, item in self._windows.items() if item.expires_at <= ts]
        for key in stale:
            self._windows.pop(key, None)

    def enable(
        self,
        *,
        user_id: str,
        channel: str,
        chat_id: str,
        ttl_seconds: Optional[int] = None,
    ) -> dict:
        now = time.time()
        self._cleanup(now)
        ttl = self.ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        item = SudoWindow(
            user_id=str(user_id),
            channel=str(channel),
            chat_id=str(chat_id),
            created_at=now,
            expires_at=now + ttl,
        )
        self._windows[self._key(item.user_id, item.channel, item.chat_id)] = item
        return {
            "enabled": True,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
            "ttl_seconds": ttl,
        }

    def disable(self, *, user_id: str, channel: str, chat_id: str) -> bool:
        self._cleanup()
        key = self._key(str(user_id), str(channel), str(chat_id))
        return self._windows.pop(key, None) is not None

    def status(self, *, user_id: str, channel: str, chat_id: str) -> dict:
        now = time.time()
        self._cleanup(now)
        key = self._key(str(user_id), str(channel), str(chat_id))
        item = self._windows.get(key)
        if not item:
            return {"enabled": False, "remaining_seconds": 0, "expires_at": None}
        remaining = max(0, int(item.expires_at - now))
        return {
            "enabled": remaining > 0,
            "remaining_seconds": remaining,
            "expires_at": item.expires_at,
        }

    def is_enabled(self, *, user_id: str, channel: str, chat_id: str) -> bool:
        return bool(self.status(user_id=user_id, channel=channel, chat_id=chat_id).get("enabled"))
