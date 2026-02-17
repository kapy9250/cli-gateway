"""Privileged system service (Unix socket RPC) for system actions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Set


class SystemServiceServer:
    """Execute structured system actions after grant verification."""

    def __init__(
        self,
        *,
        socket_path: str,
        executor,
        grant_manager=None,
        request_timeout_seconds: float = 15.0,
        max_request_bytes: int = 131072,
        require_grant_ops: Optional[Set[str]] = None,
    ):
        self.socket_path = str(socket_path)
        self.executor = executor
        self.grant_manager = grant_manager
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_request_bytes = max(1024, int(max_request_bytes))
        self.require_grant_ops = set(
            require_grant_ops
            or {
                "cron_upsert",
                "cron_delete",
                "docker_exec",
                "config_write",
                "config_append",
                "config_delete",
                "config_rollback",
            }
        )
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        sock = Path(self.socket_path)
        sock.parent.mkdir(parents=True, exist_ok=True)
        if sock.exists():
            sock.unlink()
        self._server = await asyncio.start_unix_server(self._handle_conn, path=self.socket_path)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        sock = Path(self.socket_path)
        if sock.exists():
            sock.unlink()

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.request_timeout_seconds)
            if not raw:
                await self._reply(writer, {"ok": False, "reason": "empty_request"})
                return
            if len(raw) > self.max_request_bytes:
                await self._reply(writer, {"ok": False, "reason": "request_too_large"})
                return
            try:
                req = json.loads(raw.decode("utf-8"))
            except Exception as e:
                await self._reply(writer, {"ok": False, "reason": f"request_decode_failed:{e}"})
                return
            result = self._process_request(req)
            await self._reply(writer, result)
        except Exception as e:
            await self._reply(writer, {"ok": False, "reason": f"handler_error:{e}"})
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _reply(self, writer: asyncio.StreamWriter, payload: Dict[str, object]) -> None:
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        writer.write(wire.encode("utf-8"))
        await writer.drain()

    def _requires_grant(self, action: dict) -> bool:
        op = str((action or {}).get("op", "")).strip().lower()
        if op in self.require_grant_ops:
            return True
        if op == "read_file":
            path = str((action or {}).get("path", ""))
            try:
                return bool(self.executor.is_sensitive_path(path))
            except Exception:
                return True
        return False

    def _verify_grant(self, req: dict, action: dict) -> Optional[Dict[str, object]]:
        if not self._requires_grant(action):
            return None
        if self.grant_manager is None:
            return {"ok": False, "reason": "grant_required_but_unavailable"}

        token = req.get("grant")
        if not token:
            return {"ok": False, "reason": "grant_required"}

        ok, reason, _ = self.grant_manager.verify(
            str(token),
            str(req.get("user_id", "")),
            action,
            consume=True,
        )
        if not ok:
            return {"ok": False, "reason": f"grant_invalid:{reason}"}
        return None

    def _process_request(self, req) -> Dict[str, object]:
        if not isinstance(req, dict):
            return {"ok": False, "reason": "request_not_object"}
        action = req.get("action")
        if not isinstance(action, dict):
            return {"ok": False, "reason": "action_not_object"}
        if not req.get("user_id"):
            return {"ok": False, "reason": "user_id_required"}

        grant_err = self._verify_grant(req, action)
        if grant_err:
            return grant_err

        result = self._execute_action(action)
        if not isinstance(result, dict):
            return {"ok": False, "reason": "executor_result_not_object"}
        return result

    def _execute_action(self, action: dict) -> Dict[str, object]:
        op = str(action.get("op", "")).strip().lower()

        if op == "journal":
            return self.executor.read_journal(
                unit=action.get("unit"),
                lines=int(action.get("lines", 100)),
                since=action.get("since"),
            )
        if op == "read_file":
            return self.executor.read_file(
                str(action.get("path", "")),
                max_bytes=action.get("max_bytes"),
            )
        if op == "cron_list":
            return self.executor.cron_list()
        if op == "cron_upsert":
            return self.executor.cron_upsert(
                name=str(action.get("name", "")),
                schedule=str(action.get("schedule", "")),
                command=str(action.get("command", "")),
                user=str(action.get("user", "root")),
            )
        if op == "cron_delete":
            return self.executor.cron_delete(name=str(action.get("name", "")))
        if op == "docker_exec":
            args = action.get("args") or []
            if not isinstance(args, list):
                return {"ok": False, "reason": "docker_args_not_list"}
            return self.executor.docker_exec([str(a) for a in args])
        if op == "config_write":
            return self.executor.write_file(
                str(action.get("path", "")),
                str(action.get("content", "")),
                append=False,
                create_backup=True,
            )
        if op == "config_append":
            return self.executor.write_file(
                str(action.get("path", "")),
                str(action.get("content", "")),
                append=True,
                create_backup=True,
            )
        if op == "config_delete":
            return self.executor.delete_file(str(action.get("path", "")))
        if op == "config_rollback":
            return self.executor.restore_file(
                str(action.get("path", "")),
                str(action.get("backup_path", "")),
            )
        return {"ok": False, "reason": "op_not_supported"}
