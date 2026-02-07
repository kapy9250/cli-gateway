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
        auth = Auth(config['auth']['allowed_users'])
        
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
        
        # TODO: Add Codex and Gemini agents in Phase 3
        
        if not agents:
            logger.error("❌ No agents enabled")
            sys.exit(1)
        
        # Session Manager
        session_manager = SessionManager(config['session'], agents)
        
        # Router
        router = Router(auth, session_manager, agents)
        
        # Telegram Channel
        telegram = TelegramChannel(config['channels']['telegram'])
        
        # Wire up router callback
        async def send_to_telegram(chat_id: str, text: str):
            await telegram.send_text(chat_id, text)
        
        router.set_send_callback(send_to_telegram)
        
        # Wire up message handler
        telegram.set_message_handler(router.handle_message)
        
        logger.info("✅ All components initialized")
        
        # Print startup banner
        print("╔════════════════════════════════════════╗")
        print("║         CLI Gateway v0.1.0             ║")
        print("╠════════════════════════════════════════╣")
        print("║ Channels:                              ║")
        print("║   ✅ Telegram                          ║")
        print("║ Agents:                                ║")
        for name in agents.keys():
            print(f"║   ✅ {name.capitalize():30s}       ║")
        print(f"║ Authorized users: {len(auth.allowed_users):2d}                  ║")
        print(f"║ Workspace: {str(workspace_base):24s}║")
        print("╚════════════════════════════════════════╝")
        
        # Start Telegram
        await telegram.start()
        logger.info("🚀 CLI Gateway is running")
        
        # Wait for shutdown signal
        shutdown_event = asyncio.Event()
        
        def signal_handler(sig, frame):
            logger.info(f"Received signal {sig}, shutting down...")
            shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Keep running
        await shutdown_event.wait()
        
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
