"""Session management for scoped active sessions."""

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
    scope_id: str
    agent_name: str
    created_at: float
    last_active: float
    work_dir: Optional[str] = None
    model: Optional[str] = None  # Model name (short alias)
    params: Optional[Dict[str, str]] = None  # Custom parameters
    name: Optional[str] = None  # Human-readable session label
    history: Optional[List[Dict[str, str]]] = None  # Conversation history [{role, content}]

    def __post_init__(self):
        if self.params is None:
            self.params = {}
        if self.history is None:
            self.history = []


class SessionManager:
    """Manage active session for each chat scope and persist metadata."""

    def __init__(self, workspace_base: Path, max_sessions_per_user: int = 5, cleanup_inactive_after_hours: int = 24):
        self.workspace_base = workspace_base
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.state_file = self.workspace_base / ".sessions.json"
        self.max_sessions_per_user = max_sessions_per_user
        self.cleanup_inactive_after_hours = cleanup_inactive_after_hours

        self.sessions: Dict[str, ManagedSession] = {}
        self.active_by_scope: Dict[str, str] = {}
        # Backward-compatibility index for older call sites/tests.
        self.active_by_user: Dict[str, str] = {}
        self._save_lock = threading.Lock()
        self._load()

    @staticmethod
    def _legacy_scope_for_user(user_id: str) -> str:
        return f"legacy:user:{str(user_id)}"

    @staticmethod
    def _extract_dm_user(scope_id: str) -> Optional[str]:
        parts = str(scope_id).split(":", 2)
        if len(parts) == 3 and parts[1] == "dm":
            return parts[2]
        return None

    def _load(self) -> None:
        if not self.state_file.exists():
            return

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.active_by_scope = {
                str(scope_id): str(session_id)
                for scope_id, session_id in data.get("active_by_scope", {}).items()
            }
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
                    scope_id=str(item.get("scope_id") or self._legacy_scope_for_user(item["user_id"])),
                    agent_name=str(item["agent_name"]),
                    created_at=float(item["created_at"]),
                    last_active=float(item["last_active"]),
                    work_dir=item.get("work_dir"),
                    model=item.get("model"),
                    params=item.get("params", {}),
                    name=item.get("name"),
                    history=item.get("history", []),
                )
            self.sessions = loaded
            # Keep legacy active_by_user functional after introducing active_by_scope.
            if not self.active_by_user:
                for session in self.sessions.values():
                    self.active_by_user[str(session.user_id)] = session.session_id
            logger.info("Loaded %d sessions from disk", len(self.sessions))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load session state from %s", self.state_file)

    def _save(self) -> None:
        payload = {
            "active_by_scope": self.active_by_scope,
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
        scope_id: Optional[str] = None,
        work_dir: Optional[str] = None,
        model: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> ManagedSession:
        """Create and activate a new session for scope."""
        user = str(user_id)
        scope = str(scope_id or user)
        existing = self.list_scope_sessions(scope) if scope else self.list_user_sessions(user)
        if self.max_sessions_per_user > 0 and len(existing) >= self.max_sessions_per_user:
            # Remove the oldest inactive session for this scope.
            oldest = sorted(existing, key=lambda s: s.last_active)[0]
            self.destroy_session(oldest.session_id)

        sid = session_id or self.generate_session_id()
        if sid in self.sessions:
            session = self.sessions[sid]
            session.user_id = user
            session.chat_id = str(chat_id)
            session.scope_id = scope
            session.agent_name = str(agent_name)
            session.last_active = time.time()
            if work_dir:
                session.work_dir = str(work_dir)
            if model is not None:
                session.model = model
            if params is not None:
                session.params = params
            self.active_by_scope[scope] = sid
            self.active_by_user[user] = sid
            self._save()
            return session

        now = time.time()
        session = ManagedSession(
            session_id=sid,
            user_id=user,
            chat_id=str(chat_id),
            scope_id=scope,
            agent_name=agent_name,
            created_at=now,
            last_active=now,
            work_dir=str(work_dir) if work_dir else None,
            model=model,
            params=params or {},
        )
        self.sessions[sid] = session
        self.active_by_scope[scope] = sid
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

    def list_scope_sessions(self, scope_id: str) -> List[ManagedSession]:
        """List all sessions belonging to one chat scope."""
        scope = str(scope_id)
        scoped = [s for s in self.sessions.values() if s.scope_id == scope]
        if scoped:
            return scoped

        # Backward compatibility for pre-scope state in DM contexts.
        dm_user = self._extract_dm_user(scope)
        if dm_user is None:
            return scoped
        legacy_scope = self._legacy_scope_for_user(dm_user)
        return [
            s
            for s in self.sessions.values()
            if s.user_id == dm_user and (s.scope_id == dm_user or s.scope_id == legacy_scope)
        ]

    def list_all_sessions(self) -> List[ManagedSession]:
        """List all sessions across all users."""
        return list(self.sessions.values())

    def get_active_session(self, user_id: str) -> Optional[ManagedSession]:
        """Get active session (legacy lookup: scope first, then user)."""
        key = str(user_id)
        session_id = self.active_by_scope.get(key) or self.active_by_user.get(key)
        if not session_id:
            return None
        return self.sessions.get(session_id)

    def get_active_session_for_scope(self, scope_id: str) -> Optional[ManagedSession]:
        """Get active session for a chat scope."""
        scope = str(scope_id)
        session_id = self.active_by_scope.get(scope)
        if not session_id:
            dm_user = self._extract_dm_user(scope)
            if dm_user is not None:
                session_id = self.active_by_user.get(dm_user)
        if not session_id:
            return None
        session = self.sessions.get(session_id)
        if session is None:
            return None

        if session.scope_id == scope:
            return session
        dm_user = self._extract_dm_user(scope)
        legacy_scope = self._legacy_scope_for_user(dm_user) if dm_user is not None else None
        if dm_user and session.user_id == dm_user and session.scope_id in {dm_user, legacy_scope}:
            self.assign_scope(session.session_id, scope, activate=True)
            return self.sessions.get(session.session_id)
        return None

    def switch_session(self, user_id: str, session_id: str) -> bool:
        """Switch active session by user ownership (legacy API)."""
        session = self.sessions.get(session_id)
        if session is None or session.user_id != str(user_id):
            return False

        session.last_active = time.time()
        self.active_by_scope[session.scope_id] = session_id
        self.active_by_user[str(user_id)] = session_id
        self._save()
        return True

    def switch_session_for_scope(self, scope_id: str, session_id: str) -> bool:
        """Switch active session for one scope."""
        scope = str(scope_id)
        session = self.sessions.get(session_id)
        if session is None:
            return False

        allowed = session.scope_id == scope
        if not allowed:
            dm_user = self._extract_dm_user(scope)
            legacy_scope = self._legacy_scope_for_user(dm_user) if dm_user is not None else None
            allowed = bool(dm_user and session.user_id == dm_user and session.scope_id in {dm_user, legacy_scope})
            if allowed:
                session.scope_id = scope
        if not allowed:
            return False
        session.last_active = time.time()
        self.active_by_scope[scope] = session_id
        self.active_by_user[str(session.user_id)] = session_id
        self._save()
        return True

    def assign_scope(self, session_id: str, scope_id: str, activate: bool = True) -> bool:
        """Assign or migrate a session to a scope."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.scope_id = str(scope_id)
        session.last_active = time.time()
        if activate:
            self.active_by_scope[str(scope_id)] = session_id
            self.active_by_user[str(session.user_id)] = session_id
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
            scope_id
            for scope_id, active_session_id in self.active_by_scope.items()
            if active_session_id == session_id
        ]
        for scope_id in to_remove:
            self.active_by_scope.pop(scope_id, None)

        to_remove = [
            user_id
            for user_id, active_session_id in self.active_by_user.items()
            if active_session_id == session_id
        ]
        for user_id in to_remove:
            self.active_by_user.pop(user_id, None)

        self._save()
        return session

    def update_agent(self, session_id: str, agent_name: str) -> bool:
        """Update session agent."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.agent_name = str(agent_name)
        session.last_active = time.time()
        self._save()
        return True

    def update_work_dir(self, session_id: str, work_dir: str) -> bool:
        """Persist work_dir for a session."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.work_dir = str(work_dir)
        session.last_active = time.time()
        self._save()
        return True

    def update_model(self, session_id: str, model: Optional[str]) -> bool:
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

    def update_name(self, session_id: str, name: str) -> bool:
        """Update session human-readable name."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.name = name
        session.last_active = time.time()
        self._save()
        return True

    def add_history(self, session_id: str, role: str, content: str, max_entries: int = 20, persist: bool = True) -> None:
        """Append a history entry (prompt or response) to the session.

        Args:
            persist: If False, skip disk write (caller must call touch() or _save() later).
        """
        session = self.sessions.get(session_id)
        if session is None:
            return
        session.history.append({"role": role, "content": content[:500]})  # Truncate for storage
        # Trim to max_entries pairs (each pair = 2 entries)
        max_items = max_entries * 2
        if len(session.history) > max_items:
            session.history = session.history[-max_items:]
        if persist:
            self._save()

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session."""
        session = self.sessions.get(session_id)
        if session is None:
            return []
        return session.history

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
