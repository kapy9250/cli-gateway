"""Tests for system service Unix-socket bridge."""

from __future__ import annotations

import asyncio
import os
import stat
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


def _short_socket_path(tmp_path) -> Path:
    # AF_UNIX path length is tight on macOS; keep test socket in /tmp.
    token = uuid.uuid4().hex[:8]
    return Path("/tmp") / f"cli-gateway-test-{token}.sock"


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
