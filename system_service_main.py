#!/usr/bin/env python3
"""Privileged system service entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from core.system_executor import SystemExecutor
from core.system_grant import SystemGrantManager
from core.system_service import SystemServiceServer
from utils.helpers import load_config


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(description="CLI Gateway privileged system service")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="Override Unix socket path",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate config and print resolved system service settings, then exit",
    )
    return parser.parse_args(argv)


def setup_logging(config: dict) -> None:
    log_level = str(config.get("logging", {}).get("level", "INFO")).upper()
    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def build_server(config: dict, args) -> SystemServiceServer:
    system_cfg = config.get("system_service", {})
    socket_path = str(args.socket or system_cfg.get("socket_path", "/run/cli-gateway/system.sock"))
    grant_secret = str(system_cfg.get("grant_secret", "")).strip()
    if not grant_secret:
        raise ValueError("system_service.grant_secret is required")

    grant_manager = SystemGrantManager(
        secret=grant_secret,
        ttl_seconds=int(system_cfg.get("grant_ttl_seconds", 60)),
    )
    executor = SystemExecutor(config.get("system_ops", {}))
    require_grant_ops = set(str(v) for v in system_cfg.get("require_grant_ops", [])) or None
    allowed_peer_uids = set(int(v) for v in system_cfg.get("allowed_peer_uids", [])) or None
    return SystemServiceServer(
        socket_path=socket_path,
        executor=executor,
        grant_manager=grant_manager,
        request_timeout_seconds=float(system_cfg.get("request_timeout_seconds", 15.0)),
        max_request_bytes=int(system_cfg.get("max_request_bytes", 131072)),
        require_grant_ops=require_grant_ops,
        allowed_peer_uids=allowed_peer_uids,
        socket_mode=system_cfg.get("socket_mode"),
        socket_uid=system_cfg.get("socket_uid"),
        socket_gid=system_cfg.get("socket_gid"),
    )


async def main(argv=None):
    args = parse_cli_args(argv)
    config = load_config(args.config)
    setup_logging(config)
    logger = logging.getLogger(__name__)

    server = build_server(config, args)
    if args.validate_only:
        print("✅ system service config validation passed")
        print(f"config: {args.config}")
        print(f"socket: {server.socket_path}")
        print(f"request_timeout_seconds: {server.request_timeout_seconds}")
        print(f"max_request_bytes: {server.max_request_bytes}")
        print(f"require_grant_ops: {sorted(server.require_grant_ops)}")
        print(f"allowed_peer_uids: {sorted(server.allowed_peer_uids)}")
        print(f"socket_mode: {oct(server.socket_mode) if server.socket_mode is not None else None}")
        print(f"socket_uid: {server.socket_uid}")
        print(f"socket_gid: {server.socket_gid}")
        return

    await server.start()
    logger.info("Privileged system service listening on %s", server.socket_path)

    stop_event = asyncio.Event()

    def _on_signal(signum, _frame):
        logger.info("Received signal %s, stopping system service...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    await stop_event.wait()
    await server.stop()
    logger.info("System service stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
