"""
Base streaming CLI agent adapter.

Codex and Gemini share identical subprocess-streaming logic.
This module factors it out into a single reusable class.
"""
import asyncio
import logging
import os
import re
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
    _ROLLOUT_NOISE_RE = re.compile(
        r"(failed to record rollout items|failed to queue rollout items: channel closed|failed to shutdown rollout recorder)",
        flags=re.IGNORECASE,
    )

    @classmethod
    def _is_rollout_recorder_exit(cls, stderr: str) -> bool:
        text = str(stderr or "")
        return bool(cls._ROLLOUT_NOISE_RE.search(text))

    @classmethod
    def _user_facing_error_detail(cls, stderr: str) -> str:
        """Return a short safe error detail for chat output, omitting prompt/context dumps."""
        text = str(stderr or "")
        if not text:
            return ""
        if "[CHANNEL CONTEXT]" in text or "[SENDER CONTEXT]" in text:
            return ""

        blocked_prefixes = (
            "openai codex v",
            "workdir:",
            "model:",
            "provider:",
            "approval:",
            "sandbox:",
            "reasoning",
            "session id:",
            "mcp startup:",
            "thinking",
            "codex",
            "user",
            "[channel context]",
            "[sender context]",
        )
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            lower = line.lower()
            if cls._ROLLOUT_NOISE_RE.search(lower):
                continue
            if lower.startswith(blocked_prefixes):
                continue
            if line.startswith("@"):
                continue
            if len(line) > 280:
                line = line[:280] + "..."
            return line
        return ""

    async def create_session(
        self,
        user_id: str,
        chat_id: str,
        session_id: Optional[str] = None,
        work_dir: Optional[Path] = None,
        scope_dir: Optional[str] = None,
    ) -> SessionInfo:
        """Create or reattach a session with standard workspace structure."""
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
        logger.info(f"Created {self.agent_label} session {sid} at {work_dir}")
        return session

    async def send_message(
        self,
        session_id: str,
        message: str,
        model: str = None,
        params: dict = None,
        run_as_root: bool = False,
    ) -> AsyncIterator[str]:
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

            remote_stream = await self._remote_execute_cli_stream(
                session=session,
                command=command,
                args=args,
                env=env,
                timeout_seconds=int(timeout),
                run_as_root=bool(run_as_root),
            )
            if remote_stream is not None:
                stderr_chunks: list[str] = []
                saw_stdout_chunk = False
                try:
                    async for frame in remote_stream:
                        if not isinstance(frame, dict):
                            continue

                        event = str(frame.get("event", "done")).strip().lower()
                        if event == "heartbeat":
                            continue

                        if event == "chunk":
                            data = str(frame.get("data", "") or "")
                            if not data:
                                continue
                            stream_name = str(frame.get("stream", "stdout")).strip().lower()
                            if stream_name == "stderr":
                                stderr_chunks.append(data)
                                continue
                            saw_stdout_chunk = True
                            yield data
                            continue

                        if event not in {"done", "error"}:
                            continue

                        stdout = str(frame.get("stdout", "") or "")
                        stderr = str(frame.get("stderr", "") or "").strip()
                        if stdout and not saw_stdout_chunk:
                            yield stdout
                        if not stderr:
                            stderr = "".join(stderr_chunks).strip()
                        if stderr:
                            logger.warning(f"{self.agent_label} stderr: {stderr}")

                        if (
                            bool(frame.get("returncode", 0)) == 1
                            and saw_stdout_chunk
                            and self._is_rollout_recorder_exit(stderr)
                        ):
                            # Codex CLI may emit final content but exit 1 due rollout-recorder shutdown noise.
                            # Keep the successful streamed answer and suppress misleading failure tail.
                            return

                        if bool(frame.get("ok", False)):
                            return

                        reason = str(frame.get("reason", "")).strip()
                        if not reason:
                            if frame.get("timed_out"):
                                reason = "agent_cli_timeout"
                            elif "returncode" in frame:
                                reason = f"exit_code:{frame.get('returncode')}"
                            else:
                                reason = "remote_exec_failed"
                        yield f"❌ 远程执行失败: {reason}"
                        detail = self._user_facing_error_detail(stderr)
                        if detail:
                            yield f"\nError: {detail}"
                        return

                    # Stream ended without terminal frame.
                    yield "❌ 远程执行失败: empty_response"
                    return
                finally:
                    aclose = getattr(remote_stream, "aclose", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception:
                            pass

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
