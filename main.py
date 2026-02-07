#!/usr/bin/env python3
"""
CLI Gateway - Main entry point
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

from utils.helpers import load_config
from core.auth import Auth
from core.session import SessionManager
from core.router import Router
from agents.claude_code import ClaudeCodeAgent
from agents.codex_cli import CodexAgent
from agents.gemini_cli import GeminiAgent
from channels.telegram import TelegramChannel


# Configure logging
def setup_logging(config: dict):
    """Setup logging based on configuration"""
    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO'))
    log_file = log_config.get('file', './logs/gateway.log')
    
    # Create logs directory
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reduce noise from libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)


async def main():
    """Main application entry point"""
    
    # Load configuration
    try:
        config = load_config('config.yaml')
        logger = logging.getLogger(__name__)
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        sys.exit(1)
    
    # Setup logging
    setup_logging(config)
    logger.info("Starting CLI Gateway...")
    
    # Initialize components
    try:
        # Auth
        auth = Auth(
            config['auth']['allowed_users'],
            config['auth'].get('allowed_chats'),
            config['auth'].get('max_requests_per_minute', 0),
            config['auth'].get('state_file'),
            config['auth'].get('admin_users')
        )
        
        # Agents
        agents = {}
        workspace_base = Path(config['session']['workspace_base'])
        
        # Claude Code agent
        if config['agents']['claude'].get('enabled', True):
            agents['claude'] = ClaudeCodeAgent(
                name='claude',
                config=config['agents']['claude'],
                workspace_base=workspace_base
            )
            logger.info("✅ Claude Code agent initialized")
        
        # Codex agent (Phase 3)
        if config['agents'].get('codex', {}).get('enabled', False):
            agents['codex'] = CodexAgent(
                name='codex',
                config=config['agents']['codex'],
                workspace_base=workspace_base
            )
            logger.info("✅ Codex agent initialized")
        
        # Gemini agent (Phase 3)
        if config['agents'].get('gemini', {}).get('enabled', False):
            agents['gemini'] = GeminiAgent(
                name='gemini',
                config=config['agents']['gemini'],
                workspace_base=workspace_base
            )
            logger.info("✅ Gemini agent initialized")
        
        if not agents:
            logger.error("❌ No agents enabled")
            sys.exit(1)
        
        # Session Manager
        session_cfg = config.get('session', {})
        session_manager = SessionManager(
            Path(config['session']['workspace_base']),
            session_cfg.get('max_sessions_per_user', 5),
            session_cfg.get('cleanup_inactive_after_hours', 24)
        )
        
        # Telegram Channel
        telegram = TelegramChannel(config['channels']['telegram'])
        
        # Router
        router = Router(auth, session_manager, agents, telegram, config)
        
        # Wire up message handler
        telegram.set_message_handler(router.handle_message)
        
        logger.info("✅ All components initialized")
        
        # Print startup banner (auto-width, no overflow)
        banner_lines = [
            "CLI Gateway v0.1.0",
            "Channels:",
            "  ✅ Telegram",
            "Agents:",
        ]
        banner_lines.extend([f"  ✅ {name.capitalize()}" for name in agents.keys()])
        banner_lines.append(f"Authorized users: {len(auth.allowed_users)}")
        banner_lines.append(f"Workspace: {workspace_base}")

        inner_width = max(len(line) for line in banner_lines) + 2
        top = "╔" + "═" * inner_width + "╗"
        sep = "╠" + "═" * inner_width + "╣"
        bottom = "╚" + "═" * inner_width + "╝"
        print(top)
        for i, line in enumerate(banner_lines):
            print(f"║ {line.ljust(inner_width - 1)}║")
            if i == 0:
                print(sep)
        print(bottom)
        
        # Start Telegram
        await telegram.start()
        logger.info("🚀 CLI Gateway is running")
        
        # Wait for shutdown signal
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _on_shutdown_signal(sig_name: str):
            logger.info("Received signal %s, shutting down...", sig_name)
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_shutdown_signal, sig.name)
            except NotImplementedError:
                # Fallback for platforms without add_signal_handler support
                signal.signal(sig, lambda *_: shutdown_event.set())

        async def _cleanup_task():
            while not shutdown_event.is_set():
                try:
                    session_manager.cleanup_inactive_sessions()
                except Exception as e:
                    logger.warning("Inactive session cleanup failed: %s", e)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    continue

        cleanup_task = asyncio.create_task(_cleanup_task())

        # Keep running
        await shutdown_event.wait()

        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        # Shutdown
        logger.info("Shutting down...")
        await telegram.stop()
        logger.info("✅ Shutdown complete")
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
