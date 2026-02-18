"""
Base classes for CLI agents
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, List, Dict, Any
from pathlib import Path

from utils.bwrap_sandbox import BwrapSandbox
from utils.runtime_mode import normalize_runtime_mode


@dataclass
class UsageInfo:
    """Token usage and cost info from a single agent invocation."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    duration_ms: int = 0


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


    # Standard subdirectories within a session workspace
    SUBDIR_USER = "user"        # User-uploaded files (attachments)
    SUBDIR_AI = "ai"            # AI-generated output files
    SUBDIR_SYSTEM = "system"    # System intermediate state / memory
    SUBDIR_TEMP = "system/temp" # Temporary processing area


class BaseAgent(ABC):
    """Base class for CLI agent adapters"""
    
    def __init__(
        self,
        name: str,
        config: dict,
        workspace_base: Path,
        runtime_mode: str = "session",
        instance_id: str = "default",
        sandbox_config: Optional[dict] = None,
        system_client: Optional[object] = None,
        remote_exec_required: bool = False,
    ):
        self.name = name
        self.config = config
        self.runtime_mode = normalize_runtime_mode(runtime_mode)
        self.instance_id = str(instance_id or "default").strip() or "default"
        self.workspace_base = workspace_base / name
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.sessions: Dict[str, SessionInfo] = {}
        self._last_usage: Dict[str, UsageInfo] = {}
        self._processes: Dict[str, Any] = {}  # session_id -> running subprocess
        self.system_client = system_client
        self.remote_exec_required = bool(remote_exec_required)
        self.command_sandbox = BwrapSandbox(runtime_mode=runtime_mode, sandbox_config=sandbox_config)
    
    @staticmethod
    def init_workspace(work_dir: Path) -> None:
        """Initialize session workspace with standard directory structure."""
        for subdir in (
            SessionInfo.SUBDIR_USER,
            SessionInfo.SUBDIR_AI,
            SessionInfo.SUBDIR_SYSTEM,
            SessionInfo.SUBDIR_TEMP,
        ):
            (work_dir / subdir).mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def get_user_upload_dir(work_dir: Path) -> Path:
        """Get the directory for user-uploaded files."""
        d = work_dir / SessionInfo.SUBDIR_USER
        d.mkdir(parents=True, exist_ok=True)
        return d
    
    @staticmethod
    def safe_filename(directory: Path, filename: str) -> Path:
        """Return a non-colliding path in directory. Adds _1, _2, etc. on conflict."""
        dest = directory / filename
        if not dest.exists():
            return dest
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = directory / f"{stem}_{counter}{suffix}"
            counter += 1
        return dest
    
    @staticmethod
    def get_ai_output_dir(work_dir: Path) -> Path:
        """Get the directory for AI-generated files."""
        d = work_dir / SessionInfo.SUBDIR_AI
        d.mkdir(parents=True, exist_ok=True)
        return d
    
    @staticmethod
    def get_system_dir(work_dir: Path) -> Path:
        """Get the directory for system/memory files."""
        d = work_dir / SessionInfo.SUBDIR_SYSTEM
        d.mkdir(parents=True, exist_ok=True)
        return d
    
    @staticmethod
    def get_temp_dir(work_dir: Path) -> Path:
        """Get the temporary processing directory."""
        d = work_dir / SessionInfo.SUBDIR_TEMP
        d.mkdir(parents=True, exist_ok=True)
        return d
    
    def _resolve_model(self, model_alias: Optional[str]) -> str:
        """Resolve a model alias to its full name using config['models']."""
        if not model_alias:
            return ""
        models = self.config.get('models', {})
        return models.get(model_alias, model_alias)

    def _build_args(self, message: str, session_id: str,
                    model: Optional[str] = None, params: Optional[Dict[str, str]] = None) -> List[str]:
        """Build CLI args from config template, model, and params.

        Handles placeholder substitution ({prompt}, {session_id}),
        model resolution, and parameter flag mapping.
        """
        args_template = self.config.get('args_template', [])
        args = []
        for arg in args_template:
            arg = arg.replace("{prompt}", message)
            arg = arg.replace("{session_id}", session_id)
            args.append(arg)

        if model:
            model_flag = self.config.get('supported_params', {}).get('model')
            if model_flag:
                args.extend([model_flag, self._resolve_model(model)])

        if params:
            supported = self.config.get('supported_params', {})
            for key, value in params.items():
                param_flag = supported.get(key)
                if param_flag:
                    args.extend([param_flag, str(value)])

        return args

    def _wrap_command(
        self,
        command: str,
        args: List[str],
        *,
        work_dir: Path,
        env: Dict[str, str],
    ) -> tuple[str, List[str], Dict[str, str]]:
        """Apply runtime command sandbox (bwrap) when configured/enabled."""
        return self.command_sandbox.wrap(command, args, work_dir=work_dir, env=env)

    def _build_remote_action(
        self,
        *,
        session: SessionInfo,
        command: str,
        args: List[str],
        env: Dict[str, str],
        timeout_seconds: int,
        run_as_root: bool = False,
        stream: bool = False,
    ) -> Dict[str, Any]:
        action: Dict[str, Any] = {
            "op": "agent_cli_exec",
            "agent": self.name,
            "mode": self.runtime_mode,
            "instance_id": self.instance_id,
            "command": str(command),
            "args": [str(v) for v in args],
            "cwd": str(session.work_dir),
            "env": dict(env),
            "timeout_seconds": int(timeout_seconds),
            "run_as_root": bool(run_as_root),
        }
        if stream:
            action["stream"] = True
        return action

    async def _remote_execute_cli(
        self,
        *,
        session: SessionInfo,
        command: str,
        args: List[str],
        env: Dict[str, str],
        timeout_seconds: int,
        run_as_root: bool = False,
    ) -> Optional[Dict[str, object]]:
        """Route CLI invocation through privileged system service when configured."""
        if self.system_client is None:
            if self.remote_exec_required:
                return {"ok": False, "reason": "system_client_required"}
            return None
        action = self._build_remote_action(
            session=session,
            command=command,
            args=args,
            env=env,
            timeout_seconds=int(timeout_seconds),
            run_as_root=bool(run_as_root),
            stream=False,
        )
        return await self.system_client.execute(str(session.user_id), action)

    async def _remote_execute_cli_stream(
        self,
        *,
        session: SessionInfo,
        command: str,
        args: List[str],
        env: Dict[str, str],
        timeout_seconds: int,
        run_as_root: bool = False,
    ) -> Optional[AsyncIterator[Dict[str, object]]]:
        """Route CLI invocation through system service with stream frames when supported."""
        if self.system_client is None:
            if self.remote_exec_required:
                async def _required_error() -> AsyncIterator[Dict[str, object]]:
                    yield {"event": "done", "ok": False, "reason": "system_client_required"}
                return _required_error()
            return None

        if hasattr(self.system_client, "execute_stream"):
            action = self._build_remote_action(
                session=session,
                command=command,
                args=args,
                env=env,
                timeout_seconds=int(timeout_seconds),
                run_as_root=bool(run_as_root),
                stream=True,
            )
            return self.system_client.execute_stream(str(session.user_id), action)

        single = await self._remote_execute_cli(
            session=session,
            command=command,
            args=args,
            env=env,
            timeout_seconds=int(timeout_seconds),
            run_as_root=bool(run_as_root),
        )
        if single is None:
            return None

        async def _single_frame() -> AsyncIterator[Dict[str, object]]:
            payload = dict(single if isinstance(single, dict) else {"ok": False, "reason": "response_not_object"})
            payload.setdefault("event", "done")
            yield payload

        return _single_frame()

    @abstractmethod
    async def create_session(
        self,
        user_id: str,
        chat_id: str,
        session_id: Optional[str] = None,
        work_dir: Optional[Path] = None,
        scope_dir: Optional[str] = None,
    ) -> SessionInfo:
        """
        Create a new session
        
        Returns:
            SessionInfo object
        """
        pass
    
    @abstractmethod
    async def send_message(
        self,
        session_id: str,
        message: str,
        model: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
        run_as_root: bool = False,
    ) -> AsyncIterator[str]:
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

    def get_last_usage(self, session_id: str) -> Optional[UsageInfo]:
        """Pop and return the usage info from the last send_message call."""
        return self._last_usage.pop(session_id, None)
    
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
