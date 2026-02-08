"""
Claude Code CLI agent adapter
"""
import asyncio
import logging
import os
import secrets
import uuid
import time
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

from agents.base import BaseAgent, SessionInfo

logger = logging.getLogger(__name__)


class ClaudeCodeAgent(BaseAgent):
    """
    Claude Code CLI adapter

    Uses non-interactive mode: claude -p "prompt" --session-id <sid> --output-format text
    """

    def __init__(self, name: str, config: dict, workspace_base: Path):
        super().__init__(name, config, workspace_base)
        # Track running subprocesses per session for cleanup
        self._processes: Dict[str, asyncio.subprocess.Process] = {}

    # ── process lifecycle helpers ──

    def is_process_alive(self, session_id: str) -> bool:
        """Check if a session has a running subprocess."""
        proc = self._processes.get(session_id)
        return proc is not None and proc.returncode is None

    async def kill_process(self, session_id: str) -> None:
        """Kill a running subprocess for a session and reset busy flag."""
        proc = self._processes.pop(session_id, None)
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
                logger.info("Killed orphan claude process for session %s", session_id)
            except ProcessLookupError:
                pass
        session = self.sessions.get(session_id)
        if session:
            session.is_busy = False

    # ── core interface ──

    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """Create new Claude Code session"""
        # Generate session ID
        session_id = str(uuid.uuid4())  # 8 hex chars

        # Create workspace with standard directory structure
        work_dir = self.workspace_base / f"sess_{session_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.init_workspace(work_dir)

        session = SessionInfo(
            session_id=session_id,
            agent_name=self.name,
            user_id=user_id,
            work_dir=work_dir,
            created_at=time.time(),
            last_active=time.time()
        )

        self.sessions[session_id] = session

        logger.info(f"Created Claude Code session {session_id} at {work_dir}")
        return session

    async def send_message(self, session_id: str, message: str, model: str = None, params: dict = None) -> AsyncIterator[str]:
        """
        Send message to Claude Code and stream output

        Args:
            session_id: Session ID
            message: User message/prompt
            model: Model alias (e.g. "sonnet", "opus")
            params: Custom parameters (e.g. {"thinking": "high"})
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Mark as busy
        session.is_busy = True
        session.last_active = time.time()

        process = None
        try:
            # Build command
            command = self.config['command']
            args_template = self.config.get('args_template', [])

            # Replace placeholders
            args = []
            for arg in args_template:
                arg = arg.replace("{prompt}", message)
                arg = arg.replace("{session_id}", session_id)
                args.append(arg)

            # Add model parameter if specified
            if model:
                model_flag = self.config.get('supported_params', {}).get('model')
                if model_flag:
                    # Get full model name from alias
                    models = self.config.get('models', {})
                    model_full = models.get(model, model)  # Fallback to alias if not found
                    args.extend([model_flag, model_full])

            # Add custom parameters
            if params:
                supported = self.config.get('supported_params', {})
                for key, value in params.items():
                    param_flag = supported.get(key)
                    if param_flag:
                        args.extend([param_flag, str(value)])

            # Environment
            env = os.environ.copy()
            env.update(self.config.get('env', {}))

            # Timeout
            timeout = self.config.get('timeout', 300)

            logger.info(f"Executing: {command} {' '.join(args)}")

            # Execute
            try:
                process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    cwd=str(session.work_dir),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                # Track subprocess for cleanup
                self._processes[session_id] = process

                # Stream stdout in real-time
                start_time = time.time()
                stderr_task = asyncio.create_task(process.stderr.read())

                while True:
                    # Check timeout
                    if time.time() - start_time > timeout:
                        process.kill()
                        await process.wait()
                        yield f"⚠️ 操作超时（{timeout}秒）"
                        break

                    # Read line by line
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(),
                            timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        # Check if process is still running
                        if process.returncode is not None:
                            break
                        continue

                    if not line:
                        # EOF
                        break

                    # Decode and yield
                    text = line.decode('utf-8', errors='replace')
                    if text:
                        yield text

                # Wait for process to complete
                await process.wait()

                # Check stderr
                try:
                    stderr = await asyncio.wait_for(stderr_task, timeout=1.0)
                    error = stderr.decode('utf-8', errors='replace')
                    if error:
                        logger.warning(f"Claude Code stderr: {error}")
                except asyncio.TimeoutError:
                    error = None

                if process.returncode != 0:
                    yield f"\n\n❌ Exit code: {process.returncode}"
                    if error:
                        yield f"\nError: {error}"

            except asyncio.TimeoutError:
                logger.error(f"Claude Code timeout after {timeout}s")
                yield f"⚠️ 操作超时（{timeout}秒）"
                # Try to kill process
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass

            except FileNotFoundError:
                error_msg = f"❌ Claude Code CLI 未安装或未找到命令: {command}"
                logger.error(error_msg)
                yield error_msg

            except Exception as e:
                error_msg = f"❌ 执行错误: {str(e)}"
                logger.error(error_msg, exc_info=True)
                yield error_msg

        finally:
            # Always clean up subprocess (handles premature generator close)
            self._processes.pop(session_id, None)
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                    logger.info("Killed subprocess on generator close for session %s", session_id)
                except ProcessLookupError:
                    pass
            session.is_busy = False
            session.last_active = time.time()

    async def cancel(self, session_id: str):
        """Cancel current operation"""
        await self.kill_process(session_id)

    async def destroy_session(self, session_id: str):
        """Destroy session"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Kill any running process first
        await self.kill_process(session_id)

        # Remove from sessions dict
        del self.sessions[session_id]

        logger.info(f"Destroyed session {session_id} (workspace retained at {session.work_dir})")

    def health_check(self, session_id: str) -> dict:
        """Basic health check"""
        session = self.sessions.get(session_id)

        if not session:
            return {
                "alive": False,
                "pid": None,
                "memory_mb": 0,
                "busy": False,
                "pending_seconds": None
            }

        return {
            "alive": True,
            "pid": None,  # Non-interactive mode doesn't have persistent process
            "memory_mb": 0,
            "busy": session.is_busy,
            "pending_seconds": time.time() - session.last_active if session.is_busy else None
        }
