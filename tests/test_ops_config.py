from utils.ops_config import merge_ops_config


def _base_config():
    return {
        "auth": {
            "admin_users": [286194552],
        },
        "channels": {
            "telegram": {"enabled": True, "token": "tg-token"},
            "discord": {"enabled": True, "token": "dc-token"},
            "email": {"enabled": True, "username": "ops@example.com"},
        },
        "agents": {
            "claude": {"enabled": True},
        },
        "session": {
            "workspace_base": "./workspaces",
        },
        "logging": {
            "file": "./logs/gateway.log",
            "audit": {"enabled": True, "file": "./logs/audit.log"},
        },
        "billing": {
            "dir": "./data/billing",
        },
    }


def test_merge_applies_runtime_health_and_privileged_overlay():
    base = _base_config()
    privileged = {
        "system_service": {
            "enabled": True,
            "socket_path": "/run/cli-gateway/ops-a.sock",
            "allowed_peer_uids": [999],
        },
        "system_ops": {"enabled": True, "max_read_bytes": 1024},
    }

    out, _meta = merge_ops_config(
        base,
        privileged,
        instance_id="ops-a",
        health_port=18810,
    )

    assert out["runtime"]["mode"] == "system"
    assert out["runtime"]["instance_id"] == "ops-a"
    assert out["runtime"]["namespace_paths"] is True
    assert out["health"]["host"] == "127.0.0.1"
    assert out["health"]["port"] == 18810
    assert out["system_service"]["socket_path"] == "/run/cli-gateway/ops-a.sock"
    assert out["system_service"]["enforce_peer_unit_allowlist"] is True
    assert out["system_service"]["allowed_peer_units"] == ["cli-gateway-system@ops-a.service"]
    assert out["system_service"]["require_grant_for_all_ops"] is True
    assert out["system_ops"]["max_read_bytes"] == 1024


def test_merge_generates_system_admin_totp_secret():
    base = _base_config()
    out, meta = merge_ops_config(base, {})

    assert out["auth"]["system_admin_users"] == ["286194552"]
    secret = out["two_factor"]["secrets"]["286194552"]
    assert isinstance(secret, str) and len(secret) >= 16
    assert out["two_factor"]["enabled"] is True
    assert meta["generated_secret_users"] == ["286194552"]


def test_merge_keeps_existing_system_admin_secret():
    base = _base_config()
    base["auth"]["system_admin_users"] = ["42"]
    base["two_factor"] = {
        "enabled": True,
        "secrets": {
            "42": "ABCDEF234567",
        },
    }

    out, meta = merge_ops_config(base, {})

    assert out["auth"]["system_admin_users"] == ["42"]
    assert out["two_factor"]["secrets"]["42"] == "ABCDEF234567"
    assert meta["generated_secret_users"] == []


def test_merge_channel_profile_telegram_only():
    base = _base_config()
    out, meta = merge_ops_config(base, {}, channel_profile="telegram-only")

    assert out["channels"]["telegram"]["enabled"] is True
    assert out["channels"]["discord"]["enabled"] is False
    assert out["channels"]["email"]["enabled"] is False
    assert meta["channel_enabled"] == {
        "telegram": True,
        "discord": False,
        "email": False,
    }


def test_merge_default_channel_profile_is_telegram_only():
    base = _base_config()
    out, meta = merge_ops_config(base, {})

    assert out["channels"]["telegram"]["enabled"] is True
    assert out["channels"]["discord"]["enabled"] is False
    assert out["channels"]["email"]["enabled"] is False
    assert meta["channel_profile"] == "telegram-only"


def test_merge_replaces_placeholder_totp_secret():
    base = _base_config()
    base["auth"]["system_admin_users"] = ["286194552"]
    base["two_factor"] = {
        "enabled": True,
        "secrets": {
            "286194552": "BASE32SECRETEXAMPLE",
        },
    }

    out, meta = merge_ops_config(base, {})

    assert out["two_factor"]["secrets"]["286194552"] != "BASE32SECRETEXAMPLE"
    assert meta["generated_secret_users"] == ["286194552"]


def test_merge_prefers_privileged_auth_and_two_factor():
    base = _base_config()
    base["auth"]["system_admin_users"] = ["1001"]
    base["two_factor"] = {
        "enabled": True,
        "secrets": {
            "1001": "BASE32SECRETEXAMPLE",
        },
    }
    privileged = {
        "auth": {
            "system_admin_users": ["2002"],
        },
        "two_factor": {
            "enabled": True,
            "secrets": {
                "2002": "KEEPSECRETABC2345",
            },
        },
    }

    out, meta = merge_ops_config(base, privileged)

    assert out["auth"]["system_admin_users"] == ["2002"]
    assert out["two_factor"]["secrets"]["2002"] == "KEEPSECRETABC2345"
    assert meta["generated_secret_users"] == []


def test_merge_keeps_existing_allowed_peer_units():
    base = _base_config()
    privileged = {
        "system_service": {
            "enabled": True,
            "allowed_peer_units": ["cli-gateway-system@ops-z.service"],
        }
    }
    out, _meta = merge_ops_config(base, privileged, instance_id="ops-a")
    assert out["system_service"]["allowed_peer_units"] == ["cli-gateway-system@ops-z.service"]
    assert out["system_service"]["enforce_peer_unit_allowlist"] is True
