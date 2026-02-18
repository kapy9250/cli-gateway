"""
Base streaming CLI agent adapter.

Codex and Gemini share identical subprocess-streaming logic.
This module factors it out into a single reusable class.
"""
import asyncio
import logging
import os
import uuid
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from agents.base import BaseAgent, SessionInfo

logger = logging.getLogger(__name__)


class StreamingCliAgent(BaseAgent):
    """
    Generic streaming CLI agent adapter.

    Subclasses only need to override `agent_label` for logging.
    All subprocess lifecycle, streaming, cancel, and destroy logic lives here.
    """

    agent_label: str = "CLI"  # Override in subclass for log messages

    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """Create new session with standard workspace structure."""
        session_id = str(uuid.uuid4())

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
        logger.info(f"Created {self.agent_label} session {session_id} at {work_dir}")
        return session

    async def send_message(self, session_id: str, message: str, model: str = None, params: dict = None) -> AsyncIterator[str]:
        """Send message to CLI and stream output line by line."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.is_busy = True
        session.last_active = time.time()

        try:
            command = self.config['command']
            args = self._build_args(message, session_id, model=model, params=params)

            env = os.environ.copy()
            env.update(self.config.get('env', {}))
            timeout = self.config.get('timeout', 300)

            command, args, env = self._wrap_command(
                command,
                args,
                work_dir=session.work_dir,
                env=env,
            )
            logger.info(f"Executing: {command} {' '.join(args)}")

            try:
                process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    cwd=str(session.work_dir),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                self._processes[session_id] = process

                start_time = time.time()
                stderr_task = asyncio.create_task(process.stderr.read())
                timed_out = False

                try:
                    while True:
                        if time.time() - start_time > timeout:
                            process.kill()
                            await process.wait()
                            timed_out = True
                            yield f"⚠️ 操作超时（{timeout}秒），结果可能不完整"
                            break

                        try:
                            line = await asyncio.wait_for(
                                process.stdout.readline(),
                                timeout=1.0
                            )
                        except asyncio.TimeoutError:
                            if process.returncode is not None:
                                break
                            continue

                        if not line:
                            break

                        text = line.decode('utf-8', errors='replace')
                        if text:
                            yield text

                    if not timed_out:
                        await process.wait()

                    error = None
                    try:
                        stderr = await asyncio.wait_for(stderr_task, timeout=1.0)
                        error = stderr.decode('utf-8', errors='replace')
                        if error:
                            logger.warning(f"{self.agent_label} stderr: {error}")
                    except asyncio.TimeoutError:
                        stderr_task.cancel()

                    if not timed_out and process.returncode != 0:
                        yield f"\n\n❌ Exit code: {process.returncode}"
                        if error:
                            yield f"\nError: {error}"
                finally:
                    if not stderr_task.done():
                        stderr_task.cancel()

            except asyncio.TimeoutError:
                logger.error(f"{self.agent_label} timeout after {timeout}s")
                yield f"⚠️ 操作超时（{timeout}秒）"
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

            except FileNotFoundError:
                error_msg = f"❌ {self.agent_label} CLI 未安装或未找到命令: {command}"
                logger.error(error_msg)
                yield error_msg

            except Exception as e:
                error_msg = f"❌ 执行错误: {str(e)}"
                logger.error(error_msg, exc_info=True)
                yield error_msg

        finally:
            self._processes.pop(session_id, None)
            session.is_busy = False
            session.last_active = time.time()

    async def cancel(self, session_id: str):
        """Cancel current operation by killing the subprocess."""
        process = self._processes.get(session_id)
        if process and process.returncode is None:
            try:
                process.kill()
                await process.wait()
                logger.info(f"Cancelled {self.agent_label} process for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to kill {self.agent_label} process for session {session_id}: {e}")
        session = self.sessions.get(session_id)
        if session:
            session.is_busy = False

    async def destroy_session(self, session_id: str):
        """Destroy session and kill any running subprocess."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await self.cancel(session_id)
        del self.sessions[session_id]
        logger.info(f"Destroyed session {session_id} (workspace retained at {session.work_dir})")

    def health_check(self, session_id: str) -> dict:
        """Basic health check."""
        session = self.sessions.get(session_id)
        if not session:
            return {
                "alive": False, "pid": None, "memory_mb": 0,
                "busy": False, "pending_seconds": None
            }
        return {
            "alive": True, "pid": None, "memory_mb": 0,
            "busy": session.is_busy,
            "pending_seconds": time.time() - session.last_active if session.is_busy else None
        }
