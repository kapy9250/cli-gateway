"""
Base classes for CLI agents
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, List, Dict, Any
from pathlib import Path


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
    
    @abstractmethod
    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """
        Create a new session
        
        Returns:
            SessionInfo object
        """
        pass
    
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
    
    @abstractmethod
    async def destroy_session(self, session_id: str):
        """Terminate session and optionally cleanup"""
        pass
    
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
