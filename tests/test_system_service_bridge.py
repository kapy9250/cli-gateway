"""Tests for system service Unix-socket bridge."""

from __future__ import annotations

import asyncio
import os
import stat
import time
import uuid
from pathlib import Path

import pytest

from core.system_client import SystemServiceClient
from core.system_grant import SystemGrantManager
from core.system_service import SystemServiceServer


class FakeExecutor:
    def is_sensitive_path(self, path: str) -> bool:
        return str(path).startswith("/secret")

    def read_journal(self, unit=None, lines=100, since=None):
        return {"ok": True, "unit": unit, "lines": int(lines), "output": "journal-ok"}

    def read_file(self, path: str, max_bytes=None):
        return {
            "ok": True,
            "path": str(path),
            "size_bytes": 10,
            "returned_bytes": 10,
            "truncated": False,
            "text": "file-ok",
            "sensitive": self.is_sensitive_path(path),
        }

    def docker_exec(self, args):
        return {"ok": True, "returncode": 0, "output": "docker-ok", "truncated": False, "cmd": ["docker"] + list(args)}

    def agent_cli_exec(self, action, peer_uid=None, peer_units=None):
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": "agent-ok",
            "stderr": "",
            "mode": "session",
            "instance_id": "user-main",
            "peer_uid": peer_uid,
            "peer_units": sorted(peer_units or set()),
        }


class BlockingExecutor(FakeExecutor):
    def docker_exec(self, args):
        time.sleep(1.0)
        return {"ok": True, "returncode": 0, "output": "docker-slow", "truncated": False, "cmd": ["docker"] + list(args)}


def _short_socket_path(tmp_path) -> Path:
    # AF_UNIX path length is tight on macOS; keep test socket in /tmp.
    token = uuid.uuid4().hex[:8]
    parent = Path("/tmp") / f"cli-gateway-test-{token}"
    parent.mkdir(mode=0o700, exist_ok=True)
    return parent / "system.sock"


@pytest.mark.asyncio
async def test_public_op_roundtrip_without_grant(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    server = SystemServiceServer(socket_path=str(socket_path), executor=FakeExecutor())
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        resp = await client.execute("u1", {"op": "journal", "lines": 5})
        assert resp.get("ok") is True
        assert resp.get("lines") == 5
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_protected_op_rejects_without_grant(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    grants = SystemGrantManager(secret="bridge-secret", ttl_seconds=60)
    server = SystemServiceServer(socket_path=str(socket_path), executor=FakeExecutor(), grant_manager=grants)
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        resp = await client.execute("u1", {"op": "docker_exec", "args": ["ps"]})
        assert resp.get("ok") is False
        assert resp.get("reason") == "grant_required"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_protected_op_accepts_valid_grant_and_rejects_replay(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    grants = SystemGrantManager(secret="bridge-secret", ttl_seconds=60)
    server = SystemServiceServer(socket_path=str(socket_path), executor=FakeExecutor(), grant_manager=grants)
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        action = {"op": "docker_exec", "args": ["ps"]}
        token = grants.issue("u1", action)
        ok_resp = await client.execute("u1", action, grant_token=token)
        assert ok_resp.get("ok") is True
        replay_resp = await client.execute("u1", action, grant_token=token)
        assert replay_resp.get("ok") is False
        assert "token_replayed" in str(replay_resp.get("reason"))
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_sensitive_read_requires_grant(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    grants = SystemGrantManager(secret="bridge-secret", ttl_seconds=60)
    server = SystemServiceServer(socket_path=str(socket_path), executor=FakeExecutor(), grant_manager=grants)
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        action = {"op": "read_file", "path": "/secret/token", "max_bytes": 32}
        denied = await client.execute("u1", action)
        assert denied.get("ok") is False
        assert denied.get("reason") == "grant_required"

        token = grants.issue("u1", action)
        allowed = await client.execute("u1", action, grant_token=token)
        assert allowed.get("ok") is True
        assert allowed.get("sensitive") is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_closes_active_connections(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=FakeExecutor(),
        request_timeout_seconds=60.0,
    )
    await server.start()
    try:
        _reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(b'{"partial":true}')
        await writer.drain()

        await asyncio.wait_for(server.stop(), timeout=2.0)
        await asyncio.sleep(0)
        assert not server._connections
    finally:
        # stop() is idempotent; ensure cleanup if test fails before explicit stop.
        await server.stop()


@pytest.mark.asyncio
async def test_blocking_executor_does_not_block_other_connections(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=BlockingExecutor(),
        require_grant_ops={"none"},
    )
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=3.0)

        async def _call(action: dict):
            start = time.perf_counter()
            resp = await client.execute("u1", action)
            return (time.perf_counter() - start, resp)

        slow_task = asyncio.create_task(_call({"op": "docker_exec", "args": ["ps"]}))
        await asyncio.sleep(0.05)
        fast_elapsed, fast_resp = await _call({"op": "journal", "lines": 1})
        slow_elapsed, slow_resp = await slow_task

        assert fast_resp.get("ok") is True
        assert slow_resp.get("ok") is True
        assert fast_elapsed < 0.5
        assert slow_elapsed >= 1.0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_start_refuses_non_socket_file_at_socket_path(tmp_path):
    socket_path = tmp_path / "not-a-socket.sock"
    socket_path.write_text("keep-me", encoding="utf-8")
    server = SystemServiceServer(socket_path=str(socket_path), executor=FakeExecutor())
    with pytest.raises(RuntimeError, match="socket_path_not_socket"):
        await server.start()


@pytest.mark.asyncio
async def test_socket_parent_mode_is_enforced(tmp_path):
    token = uuid.uuid4().hex[:8]
    parent = Path("/tmp") / f"cli-gateway-parent-{token}"
    parent.mkdir(mode=0o755, exist_ok=True)
    socket_path = parent / "service.sock"
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=FakeExecutor(),
        socket_parent_mode="0700",
    )
    await server.start()
    try:
        mode = stat.S_IMODE(os.stat(parent).st_mode)
        assert mode == 0o700
    finally:
        await server.stop()


def test_peer_uid_allowlist_policy_logic():
    server = SystemServiceServer(
        socket_path="/tmp/unused.sock",
        executor=FakeExecutor(),
        allowed_peer_uids={1001},
    )
    assert server._is_peer_uid_allowed(1001) is True
    assert server._is_peer_uid_allowed(1002) is False
    assert server._is_peer_uid_allowed(None) is False


def test_peer_uid_policy_disabled_allows_unknown_uid():
    server = SystemServiceServer(
        socket_path="/tmp/unused.sock",
        executor=FakeExecutor(),
        allowed_peer_uids=set(),
    )
    assert server._is_peer_uid_allowed(None) is True


def test_peer_unit_allowlist_policy_logic():
    server = SystemServiceServer(
        socket_path="/tmp/unused.sock",
        executor=FakeExecutor(),
        allowed_peer_units={"cli-gateway-system@ops-a.service"},
        enforce_peer_unit_allowlist=True,
    )
    assert server._is_peer_unit_allowed({"cli-gateway-system@ops-a.service"}) is True
    assert server._is_peer_unit_allowed({"other.service"}) is False
    assert server._is_peer_unit_allowed(set()) is False


def test_peer_unit_policy_disabled_allows_unknown_unit():
    server = SystemServiceServer(
        socket_path="/tmp/unused.sock",
        executor=FakeExecutor(),
        allowed_peer_units={"cli-gateway-system@ops-a.service"},
        enforce_peer_unit_allowlist=False,
    )
    assert server._is_peer_unit_allowed(set()) is True


def test_extract_peer_systemd_units_from_cgroup(monkeypatch):
    pid = 4242
    expected = "0::/system.slice/system-cli\\x2dgateway\\x2dsystem.slice/cli-gateway-system@ops-a.service\n"

    def _fake_read_text(self, *args, **kwargs):
        if str(self) == f"/proc/{pid}/cgroup":
            return expected
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "read_text", _fake_read_text)
    units = SystemServiceServer._extract_peer_systemd_units(pid)
    assert "cli-gateway-system@ops-a.service" in units


def test_socket_mode_is_applied(tmp_path):
    socket_path = tmp_path / "mode.sock"
    socket_path.touch()
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=FakeExecutor(),
        socket_mode="0640",
    )
    server._apply_socket_permissions()
    mode = stat.S_IMODE(os.stat(socket_path).st_mode)
    assert mode == 0o640


@pytest.mark.asyncio
async def test_require_grant_for_all_ops_blocks_journal_without_grant(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    grants = SystemGrantManager(secret="bridge-secret", ttl_seconds=60)
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=FakeExecutor(),
        grant_manager=grants,
        require_grant_for_all_ops=True,
    )
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        denied = await client.execute("u1", {"op": "journal", "lines": 5})
        assert denied.get("ok") is False
        assert denied.get("reason") == "grant_required"

        action = {"op": "journal", "lines": 5}
        token = grants.issue("u1", action)
        allowed = await client.execute("u1", action, grant_token=token)
        assert allowed.get("ok") is True
        assert allowed.get("lines") == 5
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_agent_cli_exec_is_exempt_from_grant_by_default(tmp_path):
    socket_path = _short_socket_path(tmp_path)
    grants = SystemGrantManager(secret="bridge-secret", ttl_seconds=60)
    server = SystemServiceServer(
        socket_path=str(socket_path),
        executor=FakeExecutor(),
        grant_manager=grants,
        require_grant_for_all_ops=True,
    )
    await server.start()
    try:
        client = SystemServiceClient(str(socket_path), timeout_seconds=2.0)
        action = {
            "op": "agent_cli_exec",
            "agent": "codex",
            "mode": "session",
            "instance_id": "user-main",
            "command": "codex",
            "args": ["exec", "hello"],
            "cwd": "/tmp",
            "env": {},
            "timeout_seconds": 30,
        }
        allowed = await client.execute("u1", action)
        assert allowed.get("ok") is True
        assert allowed.get("stdout") == "agent-ok"
    finally:
        await server.stop()
