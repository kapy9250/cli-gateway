"""Helpers for generating system-mode ops configuration."""

from __future__ import annotations

import base64
import secrets
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

_PLACEHOLDER_TOTP_SECRETS = {
    "BASE32SECRETEXAMPLE",
    "CHANGE_ME",
    "CHANGEME",
    "REPLACE_ME",
}


def generate_totp_secret() -> str:
    """Generate a base32 TOTP secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _normalize_user_list(values) -> List[str]:
    users: List[str] = []
    for raw in values or []:
        uid = str(raw).strip()
        if uid and uid not in users:
            users.append(uid)
    return users


def _merge_dicts(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def merge_ops_config(
    base_config: dict,
    privileged_config: Optional[dict] = None,
    *,
    instance_id: str = "ops-a",
    health_host: str = "127.0.0.1",
    health_port: int = 18810,
    force_system_mode: bool = True,
    namespace_paths: bool = True,
    enable_two_factor: bool = True,
    generate_missing_totp: bool = True,
    channel_profile: str = "keep",
) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Build a full ops config by combining a base gateway config and privileged settings."""
    if not isinstance(base_config, dict):
        raise ValueError("base_config must be a dict")
    if privileged_config is not None and not isinstance(privileged_config, dict):
        raise ValueError("privileged_config must be a dict or None")
    if channel_profile not in {"keep", "telegram-only"}:
        raise ValueError("channel_profile must be one of: keep, telegram-only")

    cfg: Dict[str, object] = deepcopy(base_config)
    privileged = privileged_config or {}

    # Keep existing privileged bridge settings as source of truth.
    for section in ("system_ops", "system_service"):
        value = privileged.get(section)
        if isinstance(value, dict):
            cfg[section] = deepcopy(value)

    runtime = cfg.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        cfg["runtime"] = runtime
    if force_system_mode:
        runtime["mode"] = "system"
    runtime["instance_id"] = str(instance_id)
    runtime["namespace_paths"] = bool(namespace_paths)

    health = cfg.setdefault("health", {})
    if not isinstance(health, dict):
        health = {}
        cfg["health"] = health
    health["host"] = str(health_host)
    health["port"] = int(health_port)

    auth = cfg.get("auth")
    if not isinstance(auth, dict):
        auth = {}
    privileged_auth = privileged.get("auth")
    if isinstance(privileged_auth, dict):
        auth = _merge_dicts(auth, privileged_auth)
    cfg["auth"] = auth
    system_admin_users = _normalize_user_list(auth.get("system_admin_users"))
    if not system_admin_users:
        system_admin_users = _normalize_user_list(auth.get("admin_users"))
    auth["system_admin_users"] = system_admin_users

    two_factor = cfg.get("two_factor")
    if not isinstance(two_factor, dict):
        two_factor = {}
    privileged_two_factor = privileged.get("two_factor")
    if isinstance(privileged_two_factor, dict):
        two_factor = _merge_dicts(two_factor, privileged_two_factor)
    cfg["two_factor"] = two_factor
    if enable_two_factor:
        two_factor["enabled"] = True
    two_factor.setdefault("ttl_seconds", 300)
    two_factor.setdefault("valid_window", 1)
    two_factor.setdefault("period_seconds", 30)
    two_factor.setdefault("digits", 6)

    secrets_map = two_factor.get("secrets")
    if not isinstance(secrets_map, dict):
        secrets_map = {}
    normalized_secrets: Dict[str, str] = {}
    for key, value in secrets_map.items():
        uid = str(key).strip()
        secret = str(value).strip().upper()
        if uid and secret and secret not in _PLACEHOLDER_TOTP_SECRETS:
            normalized_secrets[uid] = secret
    two_factor["secrets"] = normalized_secrets

    generated_secret_users: List[str] = []
    if generate_missing_totp:
        for uid in system_admin_users:
            if uid not in normalized_secrets:
                normalized_secrets[uid] = generate_totp_secret()
                generated_secret_users.append(uid)

    system_service = cfg.get("system_service")
    if not isinstance(system_service, dict):
        system_service = {}
        cfg["system_service"] = system_service
    system_service.setdefault("enforce_peer_uid_allowlist", True)
    allowed_peer_uids = system_service.get("allowed_peer_uids")
    if not isinstance(allowed_peer_uids, list):
        system_service["allowed_peer_uids"] = []
    system_service.setdefault("enforce_peer_unit_allowlist", True)
    allowed_peer_units = system_service.get("allowed_peer_units")
    if not isinstance(allowed_peer_units, list) or not any(str(v).strip() for v in allowed_peer_units):
        system_service["allowed_peer_units"] = [f"cli-gateway-system@{instance_id}.service"]
    system_service.setdefault("require_grant_for_all_ops", True)

    channel_enabled = {}
    channels = cfg.get("channels")
    if channel_profile == "telegram-only" and isinstance(channels, dict):
        for name, ch_cfg in channels.items():
            if not isinstance(ch_cfg, dict):
                continue
            enabled = name == "telegram"
            ch_cfg["enabled"] = enabled
            channel_enabled[str(name)] = enabled

    metadata: Dict[str, object] = {
        "system_admin_users": system_admin_users,
        "generated_secret_users": generated_secret_users,
        "channel_profile": channel_profile,
        "channel_enabled": channel_enabled,
    }
    return cfg, metadata
