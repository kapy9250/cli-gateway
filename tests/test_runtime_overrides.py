"""Tests for runtime override and path namespacing behavior."""

from __future__ import annotations

from types import SimpleNamespace

from main import apply_runtime_overrides


def _args(instance_id: str, namespace_paths: bool = True):
    return SimpleNamespace(
        mode=None,
        instance_id=instance_id,
        health_port=None,
        namespace_paths=namespace_paths,
    )


def test_namespace_paths_also_namespaces_audit_file():
    cfg = {
        "runtime": {},
        "auth": {"state_file": "./data/auth_state.json"},
        "session": {"workspace_base": "./workspaces"},
        "billing": {"dir": "./data/billing"},
        "logging": {
            "file": "./logs/gateway.log",
            "audit": {"enabled": True, "file": "./logs/audit.log"},
        },
    }

    out = apply_runtime_overrides(cfg, _args("inst-a", namespace_paths=True))
    assert out["logging"]["file"].endswith("/inst-a/gateway.log")
    assert out["logging"]["audit"]["file"].endswith("/inst-a/audit.log")


def test_namespace_paths_does_not_double_namespace_audit_file():
    cfg = {
        "runtime": {},
        "logging": {
            "file": "./logs/inst-a/gateway.log",
            "audit": {"enabled": True, "file": "./logs/inst-a/audit.log"},
        },
    }

    out = apply_runtime_overrides(cfg, _args("inst-a", namespace_paths=True))
    assert out["logging"]["file"] == "./logs/inst-a/gateway.log"
    assert out["logging"]["audit"]["file"] == "./logs/inst-a/audit.log"
