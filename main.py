#!/usr/bin/env python3
"""
CLI Gateway - Main entry point
"""
import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from utils.helpers import load_config


def parse_cli_args(argv=None):
    """Parse runtime CLI arguments."""
    parser = argparse.ArgumentParser(description="CLI Gateway")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["session", "system"],
        default=None,
        help="Runtime mode override",
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help="Instance identifier used for runtime metadata and optional path namespacing",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=None,
        help="Override health check port",
    )
    parser.add_argument(
        "--namespace-paths",
        action="store_true",
        help="Namespace writable paths by instance_id",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and print resolved runtime config, then exit",
    )
    return parser.parse_args(argv)


def seed_runtime_env(args) -> None:
    """Seed runtime env vars so config can reference instance/mode placeholders."""
    if args.instance_id:
        os.environ["CLI_GATEWAY_INSTANCE_ID"] = str(args.instance_id)
        os.environ["INSTANCE_ID"] = str(args.instance_id)
    else:
        os.environ.setdefault("CLI_GATEWAY_INSTANCE_ID", "default")
        os.environ.setdefault("INSTANCE_ID", os.environ["CLI_GATEWAY_INSTANCE_ID"])

    if args.mode:
        os.environ["CLI_GATEWAY_MODE"] = args.mode
    else:
        os.environ.setdefault("CLI_GATEWAY_MODE", "session")


def _namespace_path(path_value: str, instance_id: str, kind: str) -> str:
    path = Path(path_value)
    if kind == "dir":
        return str(path / instance_id)
    return str(path.parent / instance_id / path.name)


def apply_runtime_overrides(config: dict, args) -> dict:
    """Apply runtime CLI overrides onto loaded config."""
    if config is None:
        config = {}

    runtime = config.setdefault("runtime", {})
    runtime["mode"] = args.mode or runtime.get("mode") or "session"
    runtime["instance_id"] = str(
        args.instance_id or runtime.get("instance_id") or os.environ.get("CLI_GATEWAY_INSTANCE_ID", "default")
    )

    if args.health_port is not None:
        config.setdefault("health", {})["port"] = args.health_port

    if args.namespace_paths:
        instance_id = runtime["instance_id"]
        # Namespace writable state paths so multiple instances can run from one repo.
        path_specs = [
            ("auth", "state_file", "file"),
            ("session", "workspace_base", "dir"),
            ("billing", "dir", "dir"),
            ("logging", "file", "file"),
        ]
        for section, key, kind in path_specs:
            section_cfg = config.get(section, {})
            value = section_cfg.get(key)
            if not value:
                continue
            if instance_id in Path(str(value)).parts:
                continue
            section_cfg[key] = _namespace_path(str(value), instance_id, kind)

        # Namespace dedicated audit log too, if enabled/configured.
        audit_cfg = config.get("logging", {}).get("audit", {})
        audit_file = audit_cfg.get("file")
        if audit_file and instance_id not in Path(str(audit_file)).parts:
            audit_cfg["file"] = _namespace_path(str(audit_file), instance_id, "file")

        runtime["namespace_paths"] = True
    else:
        runtime["namespace_paths"] = bool(runtime.get("namespace_paths", False))

    return config


def print_runtime_summary(config: dict, args) -> None:
    runtime = config.get("runtime", {})
    print("✅ Config validation passed")
    print(f"config: {args.config}")
    print(f"mode: {runtime.get('mode')}")
    print(f"instance_id: {runtime.get('instance_id')}")
    print(f"namespace_paths: {runtime.get('namespace_paths')}")
    print(f"auth.state_file: {config.get('auth', {}).get('state_file')}")
    print(f"session.workspace_base: {config.get('session', {}).get('workspace_base')}")
    print(f"billing.dir: {config.get('billing', {}).get('dir')}")
    print(f"logging.file: {config.get('logging', {}).get('file')}")
    print(f"logging.audit.file: {config.get('logging', {}).get('audit', {}).get('file')}")
    print(f"system_service.enabled: {config.get('system_service', {}).get('enabled', False)}")
    print(f"system_service.socket_path: {config.get('system_service', {}).get('socket_path')}")
    print(f"health.port: {config.get('health', {}).get('port', 18800)}")


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


def setup_audit_logger(config: dict):
    """Setup dedicated JSONL audit logger if enabled."""
    audit_conf = config.get('logging', {}).get('audit', {})
    if not audit_conf.get('enabled', False):
        return None

    audit_file = audit_conf.get('file', './logs/audit.log')
    Path(audit_file).parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger('audit')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.FileHandler(audit_file)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


async def main(argv=None):
    """Main application entry point"""
    args = parse_cli_args(argv)
    seed_runtime_env(args)

    # Load configuration
    try:
        config = load_config(args.config)
        config = apply_runtime_overrides(config, args)
        logger = logging.getLogger(__name__)
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        sys.exit(1)

    if args.validate_only:
        print_runtime_summary(config, args)
        return

    # Setup logging
    setup_logging(config)
    audit_logger = setup_audit_logger(config)
    runtime = config.get("runtime", {})
    logger.info(
        "Starting CLI Gateway... mode=%s instance_id=%s config=%s",
        runtime.get("mode"),
        runtime.get("instance_id"),
        args.config,
    )

    # Delay heavy imports so --validate-only works without full runtime deps.
    from aiohttp import web
    from core.auth import Auth
    from core.billing import BillingTracker
    from core.session import SessionManager
    from core.router import Router
    from core.two_factor import TwoFactorManager
    from core.system_client import SystemServiceClient
    from core.system_executor import SystemExecutor
    from core.system_grant import SystemGrantManager
    from agents.claude_code import ClaudeCodeAgent
    from agents.codex_cli import CodexAgent
    from agents.gemini_cli import GeminiAgent
    from channels.telegram import TelegramChannel
    from channels.discord import DiscordChannel
    from channels.email import EmailChannel

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
            system_admin_users=[str(u) for u in auth_conf.get('system_admin_users', [])],
        )

        two_factor_conf = config.get('two_factor', {})
        two_factor = TwoFactorManager(
            enabled=two_factor_conf.get('enabled', False),
            ttl_seconds=two_factor_conf.get('ttl_seconds', 300),
            valid_window=two_factor_conf.get('valid_window', 1),
            period_seconds=two_factor_conf.get('period_seconds', 30),
            digits=two_factor_conf.get('digits', 6),
            secrets_by_user=two_factor_conf.get('secrets', {}),
        )
        logger.info("✅ Two-factor manager initialized (enabled=%s)", two_factor.enabled)

        system_ops_conf = config.get('system_ops', {})
        system_executor = SystemExecutor(system_ops_conf)
        logger.info("✅ System executor initialized (enabled=%s)", system_executor.enabled)

        system_service_conf = config.get("system_service", {})
        system_client = None
        system_grant = None
        if bool(system_service_conf.get("enabled", False)):
            socket_path = str(system_service_conf.get("socket_path", "/run/cli-gateway/system.sock"))
            timeout_seconds = float(system_service_conf.get("timeout_seconds", 10.0))
            system_client = SystemServiceClient(socket_path=socket_path, timeout_seconds=timeout_seconds)

            grant_secret = str(system_service_conf.get("grant_secret", "")).strip()
            if not grant_secret:
                logger.error("❌ system_service.enabled=true 但未配置 system_service.grant_secret")
                sys.exit(1)
            grant_ttl_seconds = int(system_service_conf.get("grant_ttl_seconds", 60))
            system_grant = SystemGrantManager(secret=grant_secret, ttl_seconds=grant_ttl_seconds)
            logger.info(
                "✅ System service client initialized (socket=%s timeout=%.1fs grant_ttl=%ss)",
                socket_path,
                timeout_seconds,
                grant_ttl_seconds,
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
        
        # Billing tracker (stored outside session workspace)
        billing_conf = config.get('billing', {})
        billing_dir = billing_conf.get('dir', './data/billing')
        billing = BillingTracker(billing_dir=billing_dir)
        logger.info("✅ Billing tracker initialized (dir=%s)", billing_dir)

        # Create Router and wire up each channel
        for channel_name, channel in channels:
            router = Router(
                auth,
                session_manager,
                agents,
                channel,
                config,
                billing=billing,
                two_factor=two_factor,
                system_executor=system_executor,
                system_client=system_client,
                system_grant=system_grant,
                audit_logger=audit_logger,
            )
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
        print(f"║ Instance: {runtime.get('instance_id', 'default'):24s}║")
        print(f"║ System backend: {('remote' if system_client else 'local'):19s}║")
        print("╚════════════════════════════════════════╝")
        
        # Start all channels
        for channel_name, channel in channels:
            await channel.start()
            logger.info(f"✅ {channel_name} channel started")

        # Start health-check HTTP server
        start_time = time.time()
        health_port = config.get('health', {}).get('port', 18800)

        async def health_handler(request):
            return web.json_response({
                "status": "ok",
                "uptime_seconds": round(time.time() - start_time, 1),
                "active_sessions": len(session_manager.list_all_sessions()),
                "agents": list(agents.keys()),
                "channels": [name for name, _ in channels],
            })

        health_app = web.Application()
        health_app.router.add_get("/health", health_handler)
        health_runner = web.AppRunner(health_app)
        await health_runner.setup()
        health_host = config.get('health', {}).get('host', '127.0.0.1')
        health_site = web.TCPSite(health_runner, health_host, health_port)
        await health_site.start()
        logger.info("✅ Health endpoint listening on :%d/health", health_port)

        logger.info("🚀 CLI Gateway is running")

        # Wait for shutdown signal
        shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        def _request_shutdown(sig_num: int):
            logger.info(f"Received signal {sig_num}, shutting down...")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown, int(sig))
            except NotImplementedError:
                signal.signal(sig, lambda s, _f: _request_shutdown(int(s)))
        
        # Keep running
        await shutdown_event.wait()
        
        # Shutdown all channels
        logger.info("Shutting down...")
        await health_runner.cleanup()
        for channel_name, channel in channels:
            await channel.stop()
            logger.info(f"✅ {channel_name} stopped")

        # Kill all running agent subprocesses
        for agent_name, agent in agents.items():
            for sid in list(agent.sessions.keys()):
                try:
                    await agent.destroy_session(sid)
                    logger.info(f"✅ Destroyed {agent_name} session {sid}")
                except Exception as e:
                    logger.warning(f"Failed to destroy {agent_name} session {sid}: {e}")
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
