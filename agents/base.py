"""
Base classes for CLI agents
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, List, Dict, Any
from pathlib import Path
import asyncio
import os
import signal
import shutil
import time
import uuid


@dataclass
class SessionInfo:
    """Session metadata"""
    session_id: str
    agent_name: str
    user_id: str
    work_dir: Path
    created_at: float
    last_active: float
    pid: Optional[int] = None  # Process ID for interactive mode
    is_busy: bool = False  # Is currently processing a request?
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict"""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "user_id": self.user_id,
            "work_dir": str(self.work_dir),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "pid": self.pid,
            "is_busy": self.is_busy
        }


class BaseAgent(ABC):
    """Base class for CLI agent adapters"""
    
    def __init__(self, name: str, config: dict, workspace_base: Path):
        self.name = name
        self.config = config
        self.workspace_base = workspace_base / name
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.sessions: Dict[str, SessionInfo] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._active_processes: Dict[str, asyncio.subprocess.Process] = {}
    
    @abstractmethod
    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """
        Create a new session
        
        Returns:
            SessionInfo object
        """
        pass

    def create_managed_session(self, user_id: str) -> SessionInfo:
        """Create and register a standard non-interactive session"""
        session_id = str(uuid.uuid4())
        work_dir = self.workspace_base / f"sess_{session_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        session = SessionInfo(
            session_id=session_id,
            agent_name=self.name,
            user_id=user_id,
            work_dir=work_dir,
            created_at=time.time(),
            last_active=time.time()
        )
        self.register_session(session)
        return session
    
    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> AsyncIterator[str]:
        """
        Send message to CLI and stream output
        
        Yields:
            Output chunks as they arrive
        """
        pass
    
    @abstractmethod
    async def cancel(self, session_id: str):
        """Cancel current operation (send SIGINT)"""
        pass

    async def cancel_active_process(self, session_id: str) -> None:
        """Shared cancellation logic for non-interactive subprocesses"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        process = self.get_active_process(session_id)
        if not process or process.returncode is not None:
            session.is_busy = False
            return

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                process.kill()
            await process.wait()
        finally:
            self.clear_active_process(session_id)
            session.is_busy = False
            session.last_active = time.time()
    
    @abstractmethod
    async def destroy_session(self, session_id: str):
        """Terminate session and optionally cleanup"""
        pass

    def destroy_managed_session(self, session_id: str) -> SessionInfo:
        """Shared destroy path for standard sessions"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        self.unregister_session(session_id)
        try:
            if session.work_dir.exists():
                shutil.rmtree(session.work_dir, ignore_errors=True)
        except Exception:
            pass
        return session
    
    def register_session(self, session: SessionInfo) -> None:
        """Register session and initialize concurrency lock"""
        self.sessions[session.session_id] = session
        self._session_locks[session.session_id] = asyncio.Lock()

    def unregister_session(self, session_id: str) -> None:
        """Unregister session and cleanup lock"""
        self.sessions.pop(session_id, None)
        self._session_locks.pop(session_id, None)
        self._active_processes.pop(session_id, None)

    def set_active_process(self, session_id: str, process: asyncio.subprocess.Process) -> None:
        """Track active subprocess for cancellation"""
        self._active_processes[session_id] = process

    def clear_active_process(self, session_id: str) -> None:
        """Clear active subprocess tracking"""
        self._active_processes.pop(session_id, None)

    def get_active_process(self, session_id: str) -> Optional[asyncio.subprocess.Process]:
        """Get active subprocess for a session"""
        return self._active_processes.get(session_id)

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Get session metadata"""
        return self.sessions.get(session_id)
    
    def list_sessions(self, user_id: str = None) -> List[SessionInfo]:
        """List sessions, optionally filtered by user"""
        if user_id:
            return [s for s in self.sessions.values() if s.user_id == user_id]
        return list(self.sessions.values())
    
    @abstractmethod
    def health_check(self, session_id: str) -> dict:
        """
        Check session health
        
        Returns:
            {
                "alive": bool,
                "pid": int | None,
                "memory_mb": float,
                "busy": bool,
                "pending_seconds": float | None
            }
        """
        pass
