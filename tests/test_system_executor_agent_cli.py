from pathlib import Path
from unittest.mock import Mock, patch

from core.system_executor import SystemExecutor


def _base_config(tmp_path: Path) -> dict:
    return {
        "enabled": True,
        "agent_cli": {
            "enabled": True,
            "run_as_uid": 1000,
            "run_as_gid": 1000,
            "workspace_parent": str(tmp_path / "workspaces"),
            "home_parent": str(tmp_path / "data"),
            "allowed_agents": ["codex"],
            "allowed_commands": ["codex"],
            "bwrap": {"enabled": False, "required": False},
        },
    }


def test_agent_cli_exec_accepts_session_caller_and_runs_command(tmp_path: Path):
    cfg = _base_config(tmp_path)
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "user-main" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "session",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(cwd),
        "env": {},
        "timeout_seconds": 30,
    }
    completed = Mock(returncode=0, stdout="ok", stderr="")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch(
        "subprocess.run", return_value=completed
    ) as run_mock:
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-session@user-main.service"},
        )

    assert result["ok"] is True
    assert result["stdout"] == "ok"
    run_args = run_mock.call_args[0][0]
    assert run_args[:2] == ["codex", "exec"]


def test_agent_cli_exec_rejects_mode_mismatch(tmp_path: Path):
    cfg = _base_config(tmp_path)
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "user-main" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "system",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(cwd),
        "env": {},
        "timeout_seconds": 30,
    }
    with patch("shutil.which", return_value="/usr/bin/codex"):
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-session@user-main.service"},
        )
    assert result["ok"] is False
    assert result["reason"] == "mode_mismatch"


def test_agent_cli_exec_rejects_cwd_outside_instance_workspace(tmp_path: Path):
    cfg = _base_config(tmp_path)
    executor = SystemExecutor(cfg)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "session",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(outside),
        "env": {},
        "timeout_seconds": 30,
    }
    with patch("shutil.which", return_value="/usr/bin/codex"):
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-session@user-main.service"},
        )
    assert result["ok"] is False
    assert result["reason"] == "cwd_not_in_workspace"


def test_agent_cli_exec_bwrap_uses_fixed_mount_points(tmp_path: Path):
    cfg = _base_config(tmp_path)
    cfg["agent_cli"]["bwrap"] = {"enabled": True, "required": True}
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "user-main" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "session",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(cwd),
        "env": {"FOO": "bar"},
        "timeout_seconds": 30,
    }
    completed = Mock(returncode=0, stdout="ok", stderr="")

    with (
        patch("os.geteuid", return_value=1000),
        patch("os.getegid", return_value=1000),
        patch("shutil.which", return_value="/usr/bin/codex"),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-session@user-main.service"},
        )

    assert result["ok"] is True
    run_args = run_mock.call_args[0][0]
    assert run_args[0] == "bwrap"
    assert "--uid" not in run_args
    assert "--gid" not in run_args

    joined = " ".join(run_args)
    assert f"--bind {cwd} /workspace" in joined
    assert "--setenv HOME /sandbox-home" in joined
    assert "--setenv CODEX_HOME /sandbox-home/.codex" in joined
    assert "--chdir /workspace" in joined
    assert "--tmpfs /etc" in joined
    assert "--ro-bind-try /etc /etc" not in joined
    assert "--ro-bind-try /etc/resolv.conf /etc/resolv.conf" in joined


def test_agent_cli_exec_root_requires_setpriv_for_uid_drop(tmp_path: Path):
    cfg = _base_config(tmp_path)
    cfg["agent_cli"]["bwrap"] = {"enabled": False, "required": False}
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "user-main" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "session",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(cwd),
        "env": {},
        "timeout_seconds": 30,
    }

    def _which(name: str) -> str | None:
        if name == "codex":
            return "/usr/bin/codex"
        if name == "setpriv":
            return None
        return None

    with (
        patch("os.geteuid", return_value=0),
        patch("os.getegid", return_value=0),
        patch("shutil.which", side_effect=_which),
    ):
        result = executor.agent_cli_exec(
            action,
            peer_uid=0,
            peer_units={"cli-gateway-session@user-main.service"},
        )

    assert result["ok"] is False
    assert result["reason"] == "setpriv_not_found"


def test_agent_cli_exec_allows_multiline_args(tmp_path: Path):
    cfg = _base_config(tmp_path)
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "user-main" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "session",
        "instance_id": "user-main",
        "command": "codex",
        "args": ["exec", "line1\nline2"],
        "cwd": str(cwd),
        "env": {},
        "timeout_seconds": 30,
    }
    completed = Mock(returncode=0, stdout="ok", stderr="")

    with (
        patch("shutil.which", return_value="/usr/bin/codex"),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-session@user-main.service"},
        )

    assert result["ok"] is True
    run_args = run_mock.call_args[0][0]
    assert run_args[2] == "line1\nline2"


def test_agent_cli_exec_system_mode_keeps_wider_readonly_mounts(tmp_path: Path):
    cfg = _base_config(tmp_path)
    cfg["agent_cli"]["bwrap"] = {"enabled": True, "required": True}
    executor = SystemExecutor(cfg)
    cwd = tmp_path / "workspaces" / "ops-a" / "codex" / "sess_1"
    cwd.mkdir(parents=True, exist_ok=True)

    action = {
        "op": "agent_cli_exec",
        "agent": "codex",
        "mode": "system",
        "instance_id": "ops-a",
        "command": "codex",
        "args": ["exec", "hello"],
        "cwd": str(cwd),
        "env": {},
        "timeout_seconds": 30,
    }
    completed = Mock(returncode=0, stdout="ok", stderr="")

    with (
        patch("os.geteuid", return_value=1000),
        patch("os.getegid", return_value=1000),
        patch("shutil.which", return_value="/usr/bin/codex"),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = executor.agent_cli_exec(
            action,
            peer_uid=999,
            peer_units={"cli-gateway-system@ops-a.service"},
        )

    assert result["ok"] is True
    run_args = run_mock.call_args[0][0]
    joined = " ".join(run_args)
    assert "--ro-bind-try /etc /etc" in joined
    tmpfs_targets = [run_args[i + 1] for i, token in enumerate(run_args[:-1]) if token == "--tmpfs"]
    assert "/etc" not in tmpfs_targets
