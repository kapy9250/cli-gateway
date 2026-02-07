"""
Gemini CLI agent adapter
"""
import asyncio
import logging
import os
import signal
import uuid
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from agents.base import BaseAgent, SessionInfo

logger = logging.getLogger(__name__)


class GeminiAgent(BaseAgent):
    """
    Gemini CLI adapter
    
    Uses non-interactive mode
    """
    
    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """Create new Gemini session"""
        session = self.create_managed_session(user_id)
        logger.info(f"Created Gemini session {session.session_id} at {session.work_dir}")
        return session
    
    async def send_message(self, session_id: str, message: str, model: str = None, params: dict = None) -> AsyncIterator[str]:
        """
        Send message to Gemini and stream output
        
        Args:
            session_id: Session ID
            message: User message/prompt
            model: Model alias (e.g. "gemini3")
            params: Custom parameters (e.g. {"temperature": "0.7"})
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        lock = self._session_locks.get(session_id)
        if not lock:
            raise ValueError(f"Session lock for {session_id} not found")
        if lock.locked():
            yield "⚠️ 会话正忙，请稍后重试或先取消当前任务。"
            return
        await lock.acquire()

        # Mark as busy
        session.is_busy = True
        session.last_active = time.time()
        
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
            
            logger.info(
                "Executing command=%s session=%s args_count=%d prompt_len=%d",
                command,
                session_id,
                len(args),
                len(message)
            )
            
            # Execute
            try:
                process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    cwd=str(session.work_dir),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True
                )
                self.set_active_process(session_id, process)
                
                # Stream stdout in real-time
                start_time = time.time()
                stderr_task = asyncio.create_task(process.stderr.read())
                
                while True:
                    # Check timeout
                    if time.time() - start_time > timeout:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except Exception:
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
                        logger.warning(f"Gemini stderr: {error}")
                except asyncio.TimeoutError:
                    error = None
                
                if process.returncode != 0:
                    yield f"\n\n❌ Exit code: {process.returncode}"
                    if error:
                        yield f"\nError: {error}"
            
            except asyncio.TimeoutError:
                logger.error(f"Gemini timeout after {timeout}s")
                yield f"⚠️ 操作超时（{timeout}秒）"
                # Try to kill process group
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    await process.wait()
                except Exception:
                    try:
                        process.kill()
                        await process.wait()
                    except Exception:
                        pass
            
            except FileNotFoundError:
                error_msg = f"❌ Gemini CLI 未安装或未找到命令: {command}"
                logger.error(error_msg)
                yield error_msg
            
            except Exception as e:
                error_msg = f"❌ 执行错误: {str(e)}"
                logger.error(error_msg, exc_info=True)
                yield error_msg
        
        finally:
            self.clear_active_process(session_id)
            session.is_busy = False
            session.last_active = time.time()
            if lock.locked():
                lock.release()
    
    async def cancel(self, session_id: str):
        """Cancel current operation by terminating active subprocess"""
        logger.info(f"Cancelling session {session_id}")
        await self.cancel_active_process(session_id)
    
    async def destroy_session(self, session_id: str):
        """Destroy session"""
        session = self.destroy_managed_session(session_id)
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
            "pid": None,
            "memory_mb": 0,
            "busy": session.is_busy,
            "pending_seconds": time.time() - session.last_active if session.is_busy else None
        }
