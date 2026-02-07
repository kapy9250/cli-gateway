"""
Claude Code CLI agent adapter
"""
import asyncio
import logging
import os
import secrets
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from agents.base import BaseAgent, SessionInfo

logger = logging.getLogger(__name__)


class ClaudeCodeAgent(BaseAgent):
    """
    Claude Code CLI adapter
    
    Uses non-interactive mode: claude -p "prompt" --session-id <sid> --output-format text
    """
    
    async def create_session(self, user_id: str, chat_id: str) -> SessionInfo:
        """Create new Claude Code session"""
        # Generate session ID
        session_id = secrets.token_hex(4)  # 8 hex chars
        
        # Create workspace
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
        
        self.sessions[session_id] = session
        
        logger.info(f"Created Claude Code session {session_id} at {work_dir}")
        return session
    
    async def send_message(self, session_id: str, message: str) -> AsyncIterator[str]:
        """
        Send message to Claude Code and stream output
        
        For Phase 1: Simple subprocess call, collect all output, yield once
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
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
                
                # Wait for completion with timeout
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                
                # Decode output
                output = stdout.decode('utf-8', errors='replace')
                error = stderr.decode('utf-8', errors='replace')
                
                if error:
                    logger.warning(f"Claude Code stderr: {error}")
                
                # Yield output
                if output:
                    yield output
                
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
            session.is_busy = False
            session.last_active = time.time()
    
    async def cancel(self, session_id: str):
        """Cancel current operation (not implemented in Phase 1)"""
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
            "pid": None,  # Non-interactive mode doesn't have persistent process
            "memory_mb": 0,
            "busy": session.is_busy,
            "pending_seconds": time.time() - session.last_active if session.is_busy else None
        }
