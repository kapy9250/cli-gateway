"""Privileged system service (Unix socket RPC) for system actions."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import struct
from pathlib import Path
from typing import Dict, Optional, Set, Tuple, Union


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
        require_grant_for_all_ops: bool = False,
        allowed_peer_uids: Optional[Set[int]] = None,
        allowed_peer_units: Optional[Set[str]] = None,
        enforce_peer_unit_allowlist: bool = False,
        socket_mode: Optional[Union[int, str]] = None,
        socket_parent_mode: Optional[Union[int, str]] = "0700",
        socket_uid: Optional[int] = None,
        socket_gid: Optional[int] = None,
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
        self.require_grant_for_all_ops = bool(require_grant_for_all_ops)
        self.allowed_peer_uids = set(int(v) for v in (allowed_peer_uids or set()))
        self.allowed_peer_units = {
            str(v).strip() for v in (allowed_peer_units or set()) if str(v).strip()
        }
        self.enforce_peer_unit_allowlist = bool(enforce_peer_unit_allowlist)
        self.socket_mode = self._normalize_mode(socket_mode)
        normalized_parent_mode = self._normalize_mode(socket_parent_mode)
        self.socket_parent_mode = 0o700 if normalized_parent_mode is None else normalized_parent_mode
        self.socket_uid = None if socket_uid is None else int(socket_uid)
        self.socket_gid = None if socket_gid is None else int(socket_gid)
        self._server: Optional[asyncio.AbstractServer] = None
        self._connections: Set[asyncio.StreamWriter] = set()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        self._ensure_socket_parent_permissions()
        self._remove_existing_socket(require_socket_type=True)
        self._server = await asyncio.start_unix_server(self._handle_conn, path=self.socket_path)
        self._apply_socket_permissions()

    async def stop(self) -> None:
        self._stopping = True
        srv = self._server
        if srv:
            srv.close()

        # Proactively close active connections so handler tasks can exit quickly.
        writers = list(self._connections)
        for writer in writers:
            try:
                writer.close()
            except Exception:
                pass
        if writers:
            await asyncio.gather(*(self._wait_writer_closed(w) for w in writers), return_exceptions=True)
        self._connections.clear()

        if srv:
            try:
                await asyncio.wait_for(srv.wait_closed(), timeout=2.0)
            except Exception:
                pass
            self._server = None

        try:
            self._remove_existing_socket(require_socket_type=True)
        except RuntimeError:
            pass

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._stopping:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        self._connections.add(writer)
        try:
            peer_pid, peer_uid, _peer_gid = self._extract_peer_credentials(writer)
            if not self._is_peer_uid_allowed(peer_uid):
                await self._reply(
                    writer,
                    {"ok": False, "reason": "peer_uid_not_allowed", "peer_uid": peer_uid, "peer_pid": peer_pid},
                )
                return
            peer_units = self._extract_peer_systemd_units(peer_pid)
            if not self._is_peer_unit_allowed(peer_units):
                await self._reply(
                    writer,
                    {
                        "ok": False,
                        "reason": "peer_unit_not_allowed",
                        "peer_uid": peer_uid,
                        "peer_pid": peer_pid,
                        "peer_units": sorted(peer_units),
                    },
                )
                return
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
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._process_request, req)
            await self._reply(writer, result)
        except Exception as e:
            try:
                await self._reply(writer, {"ok": False, "reason": f"handler_error:{e}"})
            except Exception:
                pass
        finally:
            self._connections.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _is_peer_uid_allowed(self, peer_uid: Optional[int]) -> bool:
        if not self.allowed_peer_uids:
            return True
        if peer_uid is None:
            return False
        return int(peer_uid) in self.allowed_peer_uids

    def _is_peer_unit_allowed(self, peer_units: Set[str]) -> bool:
        if not self.enforce_peer_unit_allowlist:
            return True
        if not self.allowed_peer_units:
            return False
        if not peer_units:
            return False
        return bool(self.allowed_peer_units.intersection(peer_units))

    @staticmethod
    def _extract_peer_credentials(writer: asyncio.StreamWriter) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """Best-effort peer UID extraction for Unix domain sockets.

        Linux uses SO_PEERCRED; BSD/macOS may expose getpeereid.
        """
        sock = writer.get_extra_info("socket")
        if sock is None:
            return None, None, None

        # Linux: ucred(pid, uid, gid)
        if hasattr(socket, "SO_PEERCRED"):
            try:
                size = struct.calcsize("3i")
                raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
                pid, uid, gid = struct.unpack("3i", raw)
                return int(pid), int(uid), int(gid)
            except Exception:
                pass

        # BSD/macOS: getpeereid()
        try:
            getpeereid = getattr(sock, "getpeereid", None)
            if callable(getpeereid):
                uid, _gid = getpeereid()
                return None, int(uid), int(_gid)
        except Exception:
            pass
        return None, None, None

    @staticmethod
    def _extract_peer_systemd_units(peer_pid: Optional[int]) -> Set[str]:
        if peer_pid is None or peer_pid <= 0:
            return set()
        cgroup_path = Path(f"/proc/{peer_pid}/cgroup")
        try:
            text = cgroup_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return set()

        units: Set[str] = set()
        for line in text.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            cg_path = parts[2].strip()
            if not cg_path:
                continue
            for segment in cg_path.split("/"):
                seg = segment.strip()
                if seg.endswith(".service"):
                    units.add(seg)
        return units

    async def _reply(self, writer: asyncio.StreamWriter, payload: Dict[str, object]) -> None:
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        writer.write(wire.encode("utf-8"))
        await writer.drain()

    @staticmethod
    def _normalize_mode(mode: Optional[Union[int, str]]) -> Optional[int]:
        if mode is None:
            return None
        if isinstance(mode, int):
            return mode
        text = str(mode).strip().lower()
        if not text:
            return None
        if text.startswith("0o"):
            text = text[2:]
        if text.startswith("0") and len(text) > 1:
            text = text[1:]
        return int(text, 8)

    def _apply_socket_permissions(self) -> None:
        p = Path(self.socket_path)
        if not p.exists():
            return
        if self.socket_mode is not None:
            os.chmod(p, self.socket_mode)
        if self.socket_uid is not None or self.socket_gid is not None:
            os.chown(
                p,
                self.socket_uid if self.socket_uid is not None else -1,
                self.socket_gid if self.socket_gid is not None else -1,
            )

    def _ensure_socket_parent_permissions(self) -> None:
        p = Path(self.socket_path).parent
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, self.socket_parent_mode)
        if self.socket_uid is not None or self.socket_gid is not None:
            os.chown(
                p,
                self.socket_uid if self.socket_uid is not None else -1,
                self.socket_gid if self.socket_gid is not None else -1,
            )

    def _remove_existing_socket(self, *, require_socket_type: bool) -> None:
        p = Path(self.socket_path)
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return
        if require_socket_type and not stat.S_ISSOCK(st.st_mode):
            raise RuntimeError(f"socket_path_not_socket:{self.socket_path}")
        try:
            os.unlink(p)
        except FileNotFoundError:
            return

    @staticmethod
    async def _wait_writer_closed(writer: asyncio.StreamWriter, timeout: float = 2.0) -> None:
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=timeout)
        except Exception:
            pass

    def _requires_grant(self, action: dict) -> bool:
        if self.require_grant_for_all_ops:
            return True
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
