from pathlib import Path

import pytest

from utils.bwrap_sandbox import BwrapSandbox


def test_wrap_bypassed_in_system_mode(tmp_path: Path):
    sandbox = BwrapSandbox(runtime_mode="system", sandbox_config={"bwrap": {"enabled": True}})
    command, args, env = sandbox.wrap(
        "codex",
        ["exec", "ping"],
        work_dir=tmp_path,
        env={},
    )
    assert command == "codex"
    assert args == ["exec", "ping"]
    assert env == {}


def test_wrap_uses_bwrap_when_probe_ok(tmp_path: Path):
    work_dir = tmp_path / "work"
    home_dir = tmp_path / "home"
    codex_home = home_dir / ".codex"
    work_dir.mkdir(parents=True)
    codex_home.mkdir(parents=True)

    sandbox = BwrapSandbox(runtime_mode="session", sandbox_config={"bwrap": {"enabled": True}})
    sandbox._probe_result = (True, "ok")

    command, args, env = sandbox.wrap(
        "codex",
        ["exec", "hello"],
        work_dir=work_dir,
        env={"HOME": str(home_dir), "CODEX_HOME": str(codex_home)},
    )

    assert command == "bwrap"
    assert "--bind" in args
    assert str(work_dir.resolve()) in args
    assert "--tmpfs" in args
    assert "/root" in args
    assert "--" in args
    idx = args.index("--")
    assert args[idx + 1 : idx + 4] == ["codex", "exec", "hello"]
    assert env["HOME"] == str(home_dir.resolve())
    assert env["CODEX_HOME"] == str(codex_home.resolve())
    assert env["TMPDIR"] == "/tmp"


def test_wrap_fallback_when_probe_failed_and_not_required(tmp_path: Path):
    sandbox = BwrapSandbox(runtime_mode="session", sandbox_config={"bwrap": {"enabled": True, "required": False}})
    sandbox._probe_result = (False, "permission denied")

    command, args, _env = sandbox.wrap(
        "gemini",
        ["-p", "hello"],
        work_dir=tmp_path,
        env={},
    )
    assert command == "gemini"
    assert args == ["-p", "hello"]


def test_wrap_raises_when_probe_failed_and_required(tmp_path: Path):
    sandbox = BwrapSandbox(runtime_mode="session", sandbox_config={"bwrap": {"enabled": True, "required": True}})
    sandbox._probe_result = (False, "permission denied")

    with pytest.raises(RuntimeError, match="bwrap unavailable"):
        sandbox.wrap(
            "claude",
            ["-p", "hello"],
            work_dir=tmp_path,
            env={},
        )
