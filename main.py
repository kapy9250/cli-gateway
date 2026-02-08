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
from channels.discord import DiscordChannel
from channels.email import EmailChannel


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
        # Auth — build per-channel allowed_users from channel configs
        channel_allowed = {}
        for ch_name, ch_conf in config.get('channels', {}).items():
            if ch_conf.get('enabled', False):
                users = ch_conf.get('allowed_users', [])
                if users:
                    channel_allowed[ch_name] = [str(u) for u in users]

        auth_conf = config.get('auth', {})
        auth = Auth(
            channel_allowed=channel_allowed,
            max_requests_per_minute=auth_conf.get('max_requests_per_minute', 0),
            state_file=auth_conf.get('state_file'),
            admin_users=[str(u) for u in auth_conf.get('admin_users', [])],
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
        session_manager = SessionManager(Path(config['session']['workspace_base']))
        
        # Channels
        channels = []
        
        # Telegram Channel
        if config['channels'].get('telegram', {}).get('enabled', True):
            telegram = TelegramChannel(config['channels']['telegram'])
            channels.append(('Telegram', telegram))
        
        # Discord Channel
        if config['channels'].get('discord', {}).get('enabled', False):
            discord_channel = DiscordChannel(config['channels']['discord'])
            channels.append(('Discord', discord_channel))
        
        # Email Channel
        if config['channels'].get('email', {}).get('enabled', False):
            email_channel = EmailChannel(config['channels']['email'])
            channels.append(('Email', email_channel))
        
        if not channels:
            logger.error("❌ No channels enabled")
            sys.exit(1)
        
        # Create Router and wire up each channel
        for channel_name, channel in channels:
            router = Router(auth, session_manager, agents, channel, config)
            channel.set_message_handler(router.handle_message)
        
        logger.info("✅ All components initialized")
        
        # Print startup banner
        print("╔════════════════════════════════════════╗")
        print("║         CLI Gateway v0.1.0             ║")
        print("╠════════════════════════════════════════╣")
        print("║ Channels:                              ║")
        for channel_name, _ in channels:
            print(f"║   ✅ {channel_name:30s}       ║")
        print("║ Agents:                                ║")
        for name in agents.keys():
            print(f"║   ✅ {name.capitalize():30s}       ║")
        print(f"║ Authorized users: {len(auth.allowed_users):2d}                  ║")
        print(f"║ Workspace: {str(workspace_base):24s}║")
        print("╚════════════════════════════════════════╝")
        
        # Start all channels
        for channel_name, channel in channels:
            await channel.start()
            logger.info(f"✅ {channel_name} channel started")
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
        
        # Shutdown all channels
        logger.info("Shutting down...")
        for channel_name, channel in channels:
            await channel.stop()
            logger.info(f"✅ {channel_name} stopped")
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
