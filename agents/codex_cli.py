"""
Codex CLI agent adapter
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


class CodexAgent(BaseAgent):
    """
    Codex CLI adapter
    
    Uses non-interactive mode similar to Claude Code
    """
    
    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """Create new Codex session"""
        # Generate session ID
        session_id = str(uuid.uuid4())
        
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
        
        logger.info(f"Created Codex session {session_id} at {work_dir}")
        return session
    
    async def send_message(self, session_id: str, message: str, model: str = None, params: dict = None) -> AsyncIterator[str]:
        """
        Send message to Codex and stream output
        
        Args:
            session_id: Session ID
            message: User message/prompt
            model: Model alias (e.g. "gpt5.3")
            params: Custom parameters (e.g. {"temperature": "0.7"})
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        # Mark as busy
        session.is_busy = True
        session.last_active = time.time()
        
        try:
            # Build command using shared arg builder
            command = self.config['command']
            args = self._build_args(message, session_id, model=model, params=params)

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
                        logger.warning(f"Codex stderr: {error}")
                except asyncio.TimeoutError:
                    error = None
                
                if process.returncode != 0:
                    yield f"\n\n❌ Exit code: {process.returncode}"
                    if error:
                        yield f"\nError: {error}"
            
            except asyncio.TimeoutError:
                logger.error(f"Codex timeout after {timeout}s")
                yield f"⚠️ 操作超时（{timeout}秒）"
                # Try to kill process
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
            
            except FileNotFoundError:
                error_msg = f"❌ Codex CLI 未安装或未找到命令: {command}"
                logger.error(error_msg)
                yield error_msg
            
            except Exception as e:
                error_msg = f"❌ 执行错误: {str(e)}"
                logger.error(error_msg, exc_info=True)
                yield error_msg
        
        finally:
            session.is_busy = False
            session.last_active = time.time()
    
    async def cancel(self, session_id: str):
        """Cancel current operation (not implemented)"""
        logger.warning(f"Cancel not implemented for session {session_id}")
    
    async def destroy_session(self, session_id: str):
        """Destroy session"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
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
            "pid": None,
            "memory_mb": 0,
            "busy": session.is_busy,
            "pending_seconds": time.time() - session.last_active if session.is_busy else None
        }
