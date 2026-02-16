"""Session management for user active sessions."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ManagedSession:
    """Persisted session metadata."""

    session_id: str
    user_id: str
    chat_id: str
    agent_name: str
    created_at: float
    last_active: float
    model: Optional[str] = None  # Model name (short alias)
    params: Optional[Dict[str, str]] = None  # Custom parameters
    
    def __post_init__(self):
        if self.params is None:
            self.params = {}


class SessionManager:
    """Manage active session for each user and persist metadata."""

    def __init__(self, workspace_base: Path, max_sessions_per_user: int = 5, cleanup_inactive_after_hours: int = 24):
        self.workspace_base = workspace_base
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.state_file = self.workspace_base / ".sessions.json"
        self.max_sessions_per_user = max_sessions_per_user
        self.cleanup_inactive_after_hours = cleanup_inactive_after_hours

        self.sessions: Dict[str, ManagedSession] = {}
        self.active_by_user: Dict[str, str] = {}
        self._save_lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.state_file.exists():
            return

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.active_by_user = {
                str(user_id): str(session_id)
                for user_id, session_id in data.get("active_by_user", {}).items()
            }

            loaded: Dict[str, ManagedSession] = {}
            for session_id, item in data.get("sessions", {}).items():
                loaded[str(session_id)] = ManagedSession(
                    session_id=str(item["session_id"]),
                    user_id=str(item["user_id"]),
                    chat_id=str(item["chat_id"]),
                    agent_name=str(item["agent_name"]),
                    created_at=float(item["created_at"]),
                    last_active=float(item["last_active"]),
                    model=item.get("model"),
                    params=item.get("params", {}),
                )
            self.sessions = loaded
            logger.info("Loaded %d sessions from disk", len(self.sessions))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load session state from %s", self.state_file)

    def _save(self) -> None:
        payload = {
            "active_by_user": self.active_by_user,
            "sessions": {
                session_id: asdict(session)
                for session_id, session in self.sessions.items()
            },
        }
        try:
            with self._save_lock:
                tmp_file = self.state_file.with_suffix(".json.tmp")
                tmp_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_file.replace(self.state_file)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to save session state to %s", self.state_file)

    @staticmethod
    def generate_session_id() -> str:
        """Generate 8-char hex session ID."""
        return secrets.token_hex(4)

    def create_session(
        self,
        user_id: str,
        chat_id: str,
        agent_name: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> ManagedSession:
        """Create and activate a new session for user."""
        user = str(user_id)
        existing = self.list_user_sessions(user)
        if self.max_sessions_per_user > 0 and len(existing) >= self.max_sessions_per_user:
            # Remove the oldest inactive session for this user
            oldest = sorted(existing, key=lambda s: s.last_active)[0]
            self.destroy_session(oldest.session_id)

        sid = session_id or self.generate_session_id()
        now = time.time()
        session = ManagedSession(
            session_id=sid,
            user_id=str(user_id),
            chat_id=str(chat_id),
            agent_name=agent_name,
            created_at=now,
            last_active=now,
            model=model,
            params=params or {},
        )
        self.sessions[sid] = session
        self.active_by_user[user] = sid
        self._save()
        return session

    def get_session(self, session_id: str) -> Optional[ManagedSession]:
        """Get session by ID."""
        return self.sessions.get(session_id)

    def list_user_sessions(self, user_id: str) -> List[ManagedSession]:
        """List all sessions belonging to user."""
        user = str(user_id)
        return [s for s in self.sessions.values() if s.user_id == user]

    def list_all_sessions(self) -> List[ManagedSession]:
        """List all sessions across all users."""
        return list(self.sessions.values())

    def get_active_session(self, user_id: str) -> Optional[ManagedSession]:
        """Get active session for user."""
        session_id = self.active_by_user.get(str(user_id))
        if not session_id:
            return None
        return self.sessions.get(session_id)

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch user active session."""
        session = self.sessions.get(session_id)
        if session is None or session.user_id != str(user_id):
            return False

        session.last_active = time.time()
        self.active_by_user[str(user_id)] = session_id
        self._save()
        return True

    def touch(self, session_id: str) -> None:
        """Update last active time."""
        session = self.sessions.get(session_id)
        if session is None:
            return
        session.last_active = time.time()
        self._save()

    def destroy_session(self, session_id: str) -> Optional[ManagedSession]:
        """Delete session and clear active pointers if needed."""
        session = self.sessions.pop(session_id, None)
        if session is None:
            return None

        to_remove = [
            user_id
            for user_id, active_session_id in self.active_by_user.items()
            if active_session_id == session_id
        ]
        for user_id in to_remove:
            self.active_by_user.pop(user_id, None)

        self._save()
        return session
    
    def update_model(self, session_id: str, model: str) -> bool:
        """Update session model."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.model = model
        session.last_active = time.time()
        self._save()
        return True
    
    def update_param(self, session_id: str, key: str, value: str) -> bool:
        """Update session parameter."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.params[key] = value
        session.last_active = time.time()
        self._save()
        return True
    
    def reset_params(self, session_id: str, default_params: Dict[str, str]) -> bool:
        """Reset session params to defaults."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.params = default_params.copy()
        session.last_active = time.time()
        self._save()
        return True

    def cleanup_inactive_sessions(self) -> int:
        """Cleanup sessions inactive longer than configured threshold."""
        if self.cleanup_inactive_after_hours <= 0:
            return 0

        cutoff = time.time() - (self.cleanup_inactive_after_hours * 3600)
        stale_ids = [sid for sid, s in self.sessions.items() if s.last_active < cutoff]
        if not stale_ids:
            return 0

        for sid in stale_ids:
            self.destroy_session(sid)

        logger.info("Cleaned up %d inactive sessions", len(stale_ids))
        return len(stale_ids)
