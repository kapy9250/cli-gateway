"""
Claude Code CLI agent adapter
"""
import asyncio
import inspect
import json
import logging
import os
import uuid
import time
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

from agents.base import BaseAgent, SessionInfo, UsageInfo
from utils.constants import CLI_OUTPUT_FORMAT_FLAG, CLI_OUTPUT_FORMAT_JSON

logger = logging.getLogger(__name__)


class ClaudeCodeAgent(BaseAgent):
    """
    Claude Code CLI adapter

    Uses non-interactive mode: claude -p "prompt" --session-id <sid> --output-format text
    """

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
        super().__init__(
            name=name,
            config=config,
            workspace_base=workspace_base,
            runtime_mode=runtime_mode,
            instance_id=instance_id,
            sandbox_config=sandbox_config,
            system_client=system_client,
            remote_exec_required=remote_exec_required,
        )
        # Track running subprocesses per session for cleanup
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        # Track which sessions have sent their first message
        # (first call uses --session-id, subsequent calls use --resume)
        self._initialized_sessions: set = set()

    # ── process lifecycle helpers ──

    def is_process_alive(self, session_id: str) -> bool:
        """Check if a session has a running subprocess."""
        proc = self._processes.get(session_id)
        return proc is not None and proc.returncode is None

    async def _terminate_subprocess(self, proc: asyncio.subprocess.Process) -> None:
        """Terminate a subprocess, handling both sync and async-mocked kill/wait."""
        if not proc or proc.returncode is not None:
            return
        try:
            killed = proc.kill()
            if inspect.isawaitable(killed):
                await killed
        except ProcessLookupError:
            return

        waited = proc.wait()
        if inspect.isawaitable(waited):
            await waited

    async def kill_process(self, session_id: str) -> None:
        """Kill a running subprocess for a session and reset busy flag."""
        proc = self._processes.pop(session_id, None)
        if proc and proc.returncode is None:
            await self._terminate_subprocess(proc)
            logger.info("Killed orphan claude process for session %s", session_id)
        session = self.sessions.get(session_id)
        if session:
            session.is_busy = False

    # ── core interface ──

    async def create_session(
        self,
        user_id: str,
        chat_id: str,
        session_id: Optional[str] = None,
        work_dir: Optional[Path] = None,
        scope_dir: Optional[str] = None,
    ) -> SessionInfo:
        """Create or reattach Claude Code session."""
        sid = str(session_id or str(uuid.uuid4()))
        existing = self.sessions.get(sid)
        if existing is not None:
            existing.last_active = time.time()
            return existing

        if work_dir is None:
            base_dir = self.workspace_base / str(scope_dir) if scope_dir else self.workspace_base
            work_dir = base_dir / f"sess_{sid}"
        else:
            work_dir = Path(work_dir)

        # Create workspace with standard directory structure
        work_dir.mkdir(parents=True, exist_ok=True)
        self.init_workspace(work_dir)

        session = SessionInfo(
            session_id=sid,
            agent_name=self.name,
            user_id=str(user_id),
            work_dir=work_dir,
            created_at=time.time(),
            last_active=time.time()
        )

        self.sessions[sid] = session

        logger.info(f"Created Claude Code session {sid} at {work_dir}")
        return session

    async def send_message(
        self,
        session_id: str,
        message: str,
        model: str = None,
        params: dict = None,
        run_as_root: bool = False,
    ) -> AsyncIterator[str]:
        """
        Send message to Claude Code using --output-format json.

        Parses the JSON response to extract result text and usage/cost info.
        Usage info is stored in self._last_usage[session_id] for the Router
        to pick up for billing.
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

            # Replace placeholders and force --output-format json
            # First call uses --session-id, subsequent calls use --resume
            is_resume = session_id in self._initialized_sessions
            args = []
            skip_next = False
            for i, arg in enumerate(args_template):
                if skip_next:
                    skip_next = False
                    continue
                if arg == CLI_OUTPUT_FORMAT_FLAG:
                    args.append(CLI_OUTPUT_FORMAT_FLAG)
                    args.append(CLI_OUTPUT_FORMAT_JSON)
                    skip_next = True
                    continue
                if arg == "--session-id":
                    if is_resume:
                        args.append("--resume")
                    else:
                        args.append("--session-id")
                    skip_next = True
                    args.append(session_id)
                    continue
                arg = arg.replace("{prompt}", message)
                arg = arg.replace("{session_id}", session_id)
                args.append(arg)

            # Ensure --output-format json is present
            if CLI_OUTPUT_FORMAT_FLAG not in args:
                args.extend([CLI_OUTPUT_FORMAT_FLAG, CLI_OUTPUT_FORMAT_JSON])

            # Add model parameter if specified
            resolved_model = ""
            if model:
                model_flag = self.config.get('supported_params', {}).get('model')
                if model_flag:
                    models = self.config.get('models', {})
                    model_full = models.get(model, model)
                    args.extend([model_flag, model_full])
                    resolved_model = model_full

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

            remote_resp = await self._remote_execute_cli(
                session=session,
                command=command,
                args=args,
                env=env,
                timeout_seconds=int(timeout),
                run_as_root=bool(run_as_root),
            )
            if remote_resp is not None:
                if not remote_resp.get("ok", False):
                    reason = str(remote_resp.get("reason", "remote_exec_failed"))
                    yield f"❌ 远程执行失败: {reason}"
                    stderr = str(remote_resp.get("stderr", "") or "").strip()
                    if stderr:
                        yield f"\nError: {stderr}"
                    return

                raw = str(remote_resp.get("stdout", "") or "").strip()
                error = str(remote_resp.get("stderr", "") or "").strip()
                if error:
                    logger.warning(f"Claude Code stderr: {error}")
                if not raw:
                    if error:
                        yield error
                    return
            else:
                command, args, env = self._wrap_command(
                    command,
                    args,
                    work_dir=session.work_dir,
                    env=env,
                )
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

                    # Read all stdout (JSON mode outputs a single JSON blob)
                    try:
                        stdout_data, stderr_data = await asyncio.wait_for(
                            process.communicate(),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        await self._terminate_subprocess(process)
                        logger.error(f"Claude Code timeout after {timeout}s")
                        yield f"⚠️ 操作超时（{timeout}秒），结果可能不完整"
                        return

                    raw = stdout_data.decode('utf-8', errors='replace').strip()
                    error = stderr_data.decode('utf-8', errors='replace').strip()

                    if error:
                        logger.warning(f"Claude Code stderr: {error}")

                    if process.returncode != 0:
                        # Try to parse error from JSON, fallback to raw
                        yield f"❌ Exit code: {process.returncode}"
                        if raw:
                            try:
                                data = json.loads(raw)
                                yield f"\n{data.get('result', raw)}"
                            except json.JSONDecodeError:
                                yield f"\n{raw}"
                        if error:
                            yield f"\nError: {error}"
                        return

                except FileNotFoundError:
                    error_msg = f"❌ Claude Code CLI 未安装或未找到命令: {command}"
                    logger.error(error_msg)
                    yield error_msg
                    return

                except Exception as e:
                    error_msg = f"❌ 执行错误: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    yield error_msg
                    return

            # Parse JSON response
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback: treat as plain text
                logger.warning("Failed to parse Claude JSON output, yielding raw")
                yield raw
                return

            # Extract result text
            result_text = data.get('result', '')
            if result_text:
                yield result_text

            # Mark session as initialized (subsequent calls use --resume)
            self._initialized_sessions.add(session_id)

            # Extract usage info for billing
            usage = data.get('usage', {})
            model_usage = data.get('modelUsage', {})
            # Determine actual model used
            actual_model = resolved_model
            if model_usage:
                actual_model = next(iter(model_usage.keys()), resolved_model)

            self._last_usage[session_id] = UsageInfo(
                input_tokens=usage.get('input_tokens', 0),
                output_tokens=usage.get('output_tokens', 0),
                cache_read_tokens=usage.get('cache_read_input_tokens', 0),
                cache_creation_tokens=usage.get('cache_creation_input_tokens', 0),
                cost_usd=data.get('total_cost_usd', 0.0),
                model=actual_model,
                duration_ms=data.get('duration_ms', 0),
            )

        finally:
            # Always clean up subprocess (handles premature generator close)
            self._processes.pop(session_id, None)
            if process and process.returncode is None:
                await self._terminate_subprocess(process)
                logger.info("Killed subprocess on generator close for session %s", session_id)
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

        # Remove from sessions dict and initialized set
        del self.sessions[session_id]
        self._initialized_sessions.discard(session_id)

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
