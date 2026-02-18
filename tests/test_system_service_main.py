"""Tests for system_service_main build_server configuration guards."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from system_service_main import build_server


def _base_config() -> dict:
    return {
        "system_ops": {"enabled": True},
        "system_service": {
            "socket_path": "/tmp/cli-gateway-test.sock",
            "grant_secret": "test-secret",
            "enforce_peer_uid_allowlist": True,
            "allowed_peer_uids": [1000],
        },
    }


def test_build_server_requires_allowed_peer_units_when_enforced():
    config = _base_config()
    config["system_service"]["enforce_peer_unit_allowlist"] = True
    args = SimpleNamespace(socket=None)

    with pytest.raises(ValueError, match="allowed_peer_units"):
        build_server(config, args)


def test_build_server_sets_require_grant_for_all_ops():
    config = _base_config()
    config["system_service"]["enforce_peer_unit_allowlist"] = True
    config["system_service"]["allowed_peer_units"] = ["cli-gateway-system@ops-a.service"]
    config["system_service"]["require_grant_for_all_ops"] = True
    args = SimpleNamespace(socket=None)

    server = build_server(config, args)
    assert server.require_grant_for_all_ops is True
    assert server.enforce_peer_unit_allowlist is True
    assert "cli-gateway-system@ops-a.service" in server.allowed_peer_units
