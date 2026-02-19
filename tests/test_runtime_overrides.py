"""Tests for runtime override and path namespacing behavior."""

from __future__ import annotations

from types import SimpleNamespace

from main import apply_runtime_overrides, print_runtime_summary, validate_system_security_requirements


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
        "two_factor": {"state_file": "./data/two_factor_state.json"},
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
    assert out["two_factor"]["state_file"].endswith("/inst-a/two_factor_state.json")


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


def test_validate_system_security_requirements_allows_session_mode_without_2fa():
    runtime = {"mode": "session"}
    auth = SimpleNamespace(system_admin_users=set())
    two_factor = SimpleNamespace(enabled=False, secrets_by_user={})
    validate_system_security_requirements(runtime, auth, two_factor)


def test_validate_system_security_requirements_requires_2fa_in_system_mode():
    runtime = {"mode": "system"}
    auth = SimpleNamespace(system_admin_users={"123"})
    two_factor = SimpleNamespace(enabled=False, secrets_by_user={"123": "ABC"})
    try:
        validate_system_security_requirements(runtime, auth, two_factor)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "two_factor.enabled=true" in str(e)


def test_validate_system_security_requirements_requires_system_admins():
    runtime = {"mode": "system"}
    auth = SimpleNamespace(system_admin_users=set())
    two_factor = SimpleNamespace(enabled=True, secrets_by_user={})
    try:
        validate_system_security_requirements(runtime, auth, two_factor)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "auth.system_admin_users" in str(e)


def test_validate_system_security_requirements_allows_bootstrap_without_preseeded_secrets():
    runtime = {"mode": "system"}
    auth = SimpleNamespace(system_admin_users={"123", "456"})
    two_factor = SimpleNamespace(enabled=True, secrets_by_user={"123": "ABC"})
    validate_system_security_requirements(runtime, auth, two_factor)


def test_apply_runtime_overrides_normalizes_user_sys_aliases():
    cfg = {"runtime": {"mode": "user"}}
    out = apply_runtime_overrides(
        cfg,
        SimpleNamespace(mode="sys", instance_id="inst-a", health_port=None, namespace_paths=False),
    )
    assert out["runtime"]["mode"] == "system"


def test_validate_system_security_requirements_accepts_sys_alias():
    runtime = {"mode": "sys"}
    auth = SimpleNamespace(system_admin_users={"123"})
    two_factor = SimpleNamespace(enabled=True, secrets_by_user={"123": "ABC"})
    validate_system_security_requirements(runtime, auth, two_factor)


def test_print_runtime_summary_redacts_memory_dsn(capsys):
    cfg = {
        "runtime": {
            "mode": "user",
            "instance_id": "inst-a",
            "version": "test-version",
            "namespace_paths": False,
        },
        "memory": {
            "enabled": True,
            "dsn": "postgresql://alice:super-secret@db.internal:5432/cli_gateway",
            "db_path": "./data/memory.db",
        },
    }
    args = SimpleNamespace(config="config.yaml")

    print_runtime_summary(cfg, args)
    out = capsys.readouterr().out

    assert "memory.dsn: postgresql://alice:***@db.internal:5432/cli_gateway" in out
    assert "super-secret" not in out


def test_print_runtime_summary_redacts_memory_dsn_query_params(capsys):
    cfg = {
        "runtime": {
            "mode": "user",
            "instance_id": "inst-a",
            "version": "test-version",
            "namespace_paths": False,
        },
        "memory": {
            "enabled": True,
            "dsn": "postgresql://alice:super-secret@db.internal:5432/cli_gateway?password=hunter2&sslmode=require",
            "db_path": "./data/memory.db",
        },
    }
    args = SimpleNamespace(config="config.yaml")

    print_runtime_summary(cfg, args)
    out = capsys.readouterr().out

    assert (
        "memory.dsn: postgresql://alice:***@db.internal:5432/cli_gateway?password=***&sslmode=***" in out
    )
    assert "hunter2" not in out
