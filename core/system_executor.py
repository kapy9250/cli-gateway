"""System executor with read and write operations for system-mode instances."""

from __future__ import annotations

import re
import subprocess
import time
import os
import pwd
import queue
import shutil
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class SystemExecutor:
    _CRON_FIELD_RE = re.compile(r"^[A-Za-z0-9*/,\-]+$")
    _CRON_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
    _ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _UNIT_ROLE_RE = re.compile(r"^cli-gateway-(session|system|user|sys)@([A-Za-z0-9_.:-]+)\.service$")
    _CRON_SPECIAL_SCHEDULES = {
        "@reboot",
        "@yearly",
        "@annually",
        "@monthly",
        "@weekly",
        "@daily",
        "@midnight",
        "@hourly",
    }

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.max_read_bytes = max(1, int(cfg.get("max_read_bytes", 65536)))
        self.max_journal_lines = int(cfg.get("max_journal_lines", 300))
        self.max_docker_output_bytes = int(cfg.get("max_docker_output_bytes", 200000))
        default_docker_allowed = [
            "ps",
            "images",
            "logs",
            "inspect",
            "stats",
            "top",
            "version",
            "info",
        ]
        configured_allowed = cfg.get("docker_allowed_subcommands", default_docker_allowed)
        if not isinstance(configured_allowed, (list, tuple, set)):
            configured_allowed = default_docker_allowed
        self.docker_allowed_subcommands: List[str] = [
            str(cmd).strip().lower() for cmd in configured_allowed if str(cmd).strip()
        ]
        self.cron_dir = str(cfg.get("cron_dir", "/etc/cron.d"))
        self.docker_bin = str(cfg.get("docker_bin", "docker"))
        self.sensitive_read_paths: List[str] = [
            str(p) for p in cfg.get(
                "sensitive_read_paths",
                [
                    "/etc/shadow",
                    "/etc/sudoers",
                    "/etc/ssh",
                    "/root",
                    "/home",
                    "/var/lib/docker",
                ],
            )
        ]
        self.write_allowed_paths: List[str] = [
            str(p) for p in cfg.get(
                "write_allowed_paths",
                [
                    "/etc",
                    "/opt",
                    "/data",
                    "/var",
                    "/usr/local/etc",
                ],
            )
        ]
        agent_cli_cfg = cfg.get("agent_cli")
        agent_cli_cfg = agent_cli_cfg if isinstance(agent_cli_cfg, dict) else {}
        self.agent_cli_enabled = bool(agent_cli_cfg.get("enabled", True))
        self.agent_cli_max_output_bytes = max(4096, int(agent_cli_cfg.get("max_output_bytes", 512000)))
        self.agent_cli_max_timeout_seconds = max(1, int(agent_cli_cfg.get("max_timeout_seconds", 300)))
        self.agent_cli_stream_heartbeat_seconds = max(
            1.0,
            float(agent_cli_cfg.get("stream_heartbeat_seconds", 2.0)),
        )
        self.agent_cli_stream_queue_poll_seconds = max(
            0.05,
            min(float(agent_cli_cfg.get("stream_queue_poll_seconds", 0.2)), 2.0),
        )
        self.agent_cli_max_args = max(1, int(agent_cli_cfg.get("max_args", 256)))
        self.agent_cli_allowed_agents = {
            str(v).strip().lower()
            for v in agent_cli_cfg.get("allowed_agents", ["claude", "codex", "gemini"])
            if str(v).strip()
        }
        self.agent_cli_allowed_commands = {
            str(v).strip()
            for v in agent_cli_cfg.get("allowed_commands", ["claude", "codex", "gemini", "gemini-cli"])
            if str(v).strip()
        }
        self.agent_cli_allowed_env_keys = {
            str(v).strip()
            for v in agent_cli_cfg.get("allowed_env_keys", [])
            if str(v).strip()
        }
        self.agent_cli_workspace_parent = self._normalize_path(
            str(agent_cli_cfg.get("workspace_parent", "./workspaces"))
        )
        self.agent_cli_home_parent = self._normalize_path(
            str(agent_cli_cfg.get("home_parent", "./data"))
        )
        self.agent_cli_path = str(
            agent_cli_cfg.get(
                "path",
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            )
        )

        bwrap_cfg = agent_cli_cfg.get("bwrap")
        bwrap_cfg = bwrap_cfg if isinstance(bwrap_cfg, dict) else {}
        self.agent_cli_bwrap_enabled = bool(bwrap_cfg.get("enabled", True))
        self.agent_cli_bwrap_required = bool(bwrap_cfg.get("required", True))
        self.agent_cli_bwrap_command = str(bwrap_cfg.get("command", "bwrap")).strip() or "bwrap"
        self.agent_cli_bwrap_unshare_all = bool(bwrap_cfg.get("unshare_all", True))
        self.agent_cli_bwrap_unshare_user = bool(bwrap_cfg.get("unshare_user", False))
        self.agent_cli_bwrap_share_network = bool(bwrap_cfg.get("share_network", True))
        self.agent_cli_bwrap_session_workspace_only = bool(bwrap_cfg.get("session_workspace_only", True))
        self.agent_cli_bwrap_readonly_paths = [
            str(v)
            for v in bwrap_cfg.get(
                "readonly_paths",
                [
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/lib",
                    "/lib64",
                    "/etc",
                    "/run",
                    "/opt",
                ],
            )
        ]
        self.agent_cli_bwrap_session_runtime_ro_paths = [
            str(v)
            for v in bwrap_cfg.get(
                "session_runtime_ro_paths",
                [
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/lib",
                    "/lib64",
                ],
            )
        ]
        self.agent_cli_bwrap_session_etc_ro_paths = [
            str(v)
            for v in bwrap_cfg.get(
                "session_etc_ro_paths",
                [
                    "/etc/resolv.conf",
                    "/etc/ssl",
                    "/etc/ssl/certs",
                    "/etc/ca-certificates",
                    "/etc/nsswitch.conf",
                ],
            )
        ]
        self.agent_cli_bwrap_mask_dirs = [
            str(v)
            for v in bwrap_cfg.get(
                "mask_dirs",
                [
                    "/root",
                    "/etc/cron.d",
                    "/etc/cron.daily",
                    "/etc/cron.hourly",
                    "/etc/cron.monthly",
                    "/etc/cron.weekly",
                    "/var/spool/cron",
                    "/var/spool/cron/crontabs",
                ],
            )
        ]
        self.agent_cli_bwrap_mask_files = [str(v) for v in bwrap_cfg.get("mask_files", ["/etc/crontab"])]

        run_as_user = str(agent_cli_cfg.get("run_as_user", "cli-gateway")).strip() or "cli-gateway"
        run_as_uid = agent_cli_cfg.get("run_as_uid")
        run_as_gid = agent_cli_cfg.get("run_as_gid")
        self.agent_cli_run_uid, self.agent_cli_run_gid = self._resolve_run_identity(
            run_as_user,
            run_as_uid,
            run_as_gid,
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        p = Path(path).expanduser()
        try:
            return str(p.resolve(strict=False))
        except Exception:
            return str(p.absolute())

    @staticmethod
    def _canonical_mode_name(raw: str) -> str:
        value = str(raw or "").strip().lower()
        if value in {"session", "user"}:
            return "session"
        if value in {"system", "sys"}:
            return "system"
        return value

    @classmethod
    def _path_matches_prefixes(cls, path: str, prefixes: List[str]) -> bool:
        normalized = cls._normalize_path(path)
        for prefix in prefixes:
            p = cls._normalize_path(prefix).rstrip("/") or "/"
            if p == "/":
                return True
            if normalized == p or normalized.startswith(p + "/"):
                return True
        return False

    def is_sensitive_path(self, path: str) -> bool:
        return self._path_matches_prefixes(path, self.sensitive_read_paths)

    def is_write_allowed(self, path: str) -> bool:
        return self._path_matches_prefixes(path, self.write_allowed_paths)

    def _resolve_read_limit(self, requested: Optional[int]) -> int:
        """Clamp user-requested read limit to safe bounds."""
        if requested is None:
            return self.max_read_bytes
        try:
            n = int(requested)
        except Exception:
            return self.max_read_bytes
        if n <= 0:
            return self.max_read_bytes
        return min(n, self.max_read_bytes)

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}

        limit = self._resolve_read_limit(max_bytes)
        p = Path(self._normalize_path(path))
        if not p.exists():
            return {"ok": False, "reason": "file_not_found"}
        if not p.is_file():
            return {"ok": False, "reason": "not_a_file"}

        size_bytes = 0
        try:
            size_bytes = int(p.stat().st_size)
        except Exception:
            size_bytes = 0
        try:
            with p.open("rb") as fh:
                data = fh.read(limit + 1)
        except Exception as e:
            return {"ok": False, "reason": f"file_read_error:{e}"}
        truncated = len(data) > limit
        payload = data[:limit]
        text = payload.decode("utf-8", errors="replace")
        if size_bytes <= 0:
            size_bytes = len(payload) + (1 if truncated else 0)

        return {
            "ok": True,
            "path": str(p),
            "size_bytes": size_bytes,
            "returned_bytes": len(payload),
            "truncated": truncated,
            "text": text,
            "sensitive": self.is_sensitive_path(str(p)),
        }

    def write_file(
        self,
        path: str,
        content: str,
        append: bool = False,
        create_backup: bool = True,
    ) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        normalized = self._normalize_path(path)
        if not self.is_write_allowed(normalized):
            return {"ok": False, "reason": "write_path_not_allowed"}

        p = Path(normalized)
        backup_path = None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if create_backup and p.exists() and p.is_file():
                ts = time.strftime("%Y%m%d_%H%M%S")
                backup = p.with_name(f"{p.name}.bak.{ts}")
                backup.write_bytes(p.read_bytes())
                backup_path = str(backup)
            mode = "a" if append else "w"
            with p.open(mode, encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            return {"ok": False, "reason": f"write_file_error:{e}"}

        return {"ok": True, "path": str(p), "backup_path": backup_path, "append": append}

    def delete_file(self, path: str) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        normalized = self._normalize_path(path)
        if not self.is_write_allowed(normalized):
            return {"ok": False, "reason": "write_path_not_allowed"}

        p = Path(normalized)
        if not p.exists():
            return {"ok": False, "reason": "file_not_found"}
        try:
            p.unlink()
        except Exception as e:
            return {"ok": False, "reason": f"delete_file_error:{e}"}
        return {"ok": True, "path": str(p)}

    def restore_file(self, path: str, backup_path: str) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        target_path = self._normalize_path(path)
        if not self.is_write_allowed(target_path):
            return {"ok": False, "reason": "write_path_not_allowed"}
        normalized_backup_path = self._normalize_path(backup_path)
        if not self.is_write_allowed(normalized_backup_path):
            return {"ok": False, "reason": "backup_path_not_allowed"}

        target = Path(target_path)
        backup = Path(normalized_backup_path)
        if not backup.exists():
            return {"ok": False, "reason": "backup_not_found"}
        if not backup.is_file():
            return {"ok": False, "reason": "backup_not_file"}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(backup.read_bytes())
        except Exception as e:
            return {"ok": False, "reason": f"restore_file_error:{e}"}
        return {"ok": True, "path": str(target), "backup_path": str(backup)}

    @staticmethod
    def _validate_cron_name(name: str) -> bool:
        if not name:
            return False
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        return all(ch in allowed for ch in name)

    @staticmethod
    def _contains_line_break_or_nul(value: str) -> bool:
        text = str(value or "")
        return ("\n" in text) or ("\r" in text) or ("\x00" in text)

    @staticmethod
    def _contains_nul(value: str) -> bool:
        return "\x00" in str(value or "")

    @classmethod
    def _validate_cron_schedule(cls, schedule: str) -> bool:
        text = str(schedule or "").strip()
        if not text or cls._contains_line_break_or_nul(text):
            return False
        if text.startswith("@"):
            return text in cls._CRON_SPECIAL_SCHEDULES
        fields = text.split()
        if len(fields) != 5:
            return False
        return all(bool(cls._CRON_FIELD_RE.fullmatch(field)) for field in fields)

    @classmethod
    def _validate_cron_user(cls, user: str) -> bool:
        text = str(user or "").strip()
        if not text or cls._contains_line_break_or_nul(text):
            return False
        return bool(cls._CRON_USER_RE.fullmatch(text))

    @classmethod
    def _validate_cron_command(cls, command: str) -> bool:
        text = str(command or "")
        if not text.strip():
            return False
        if cls._contains_line_break_or_nul(text):
            return False
        return True

    def _cron_file_path(self, name: str) -> Optional[Path]:
        if not self._validate_cron_name(name):
            return None
        return Path(self.cron_dir) / name

    def cron_list(self) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        d = Path(self.cron_dir)
        if not d.exists():
            return {"ok": False, "reason": "cron_dir_not_found"}
        if not d.is_dir():
            return {"ok": False, "reason": "cron_dir_not_directory"}
        try:
            items = sorted(p.name for p in d.iterdir() if p.is_file())
            return {"ok": True, "items": items}
        except Exception as e:
            return {"ok": False, "reason": f"cron_list_error:{e}"}

    def cron_upsert(self, name: str, schedule: str, command: str, user: str = "root") -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        cron_path = self._cron_file_path(name)
        if cron_path is None:
            return {"ok": False, "reason": "invalid_cron_name"}
        if not self._validate_cron_schedule(schedule):
            return {"ok": False, "reason": "invalid_cron_schedule"}
        if not self._validate_cron_user(user):
            return {"ok": False, "reason": "invalid_cron_user"}
        if not self._validate_cron_command(command):
            return {"ok": False, "reason": "invalid_cron_command"}
        if not self.is_write_allowed(str(cron_path)):
            return {"ok": False, "reason": "write_path_not_allowed"}
        content = "\n".join(
            [
                "SHELL=/bin/bash",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                f"{schedule} {user} {command}",
                "",
            ]
        )
        return self.write_file(str(cron_path), content, append=False, create_backup=True)

    def cron_delete(self, name: str) -> Dict[str, object]:
        cron_path = self._cron_file_path(name)
        if cron_path is None:
            return {"ok": False, "reason": "invalid_cron_name"}
        return self.delete_file(str(cron_path))

    def docker_exec(self, args: List[str]) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}
        if not args:
            return {"ok": False, "reason": "docker_args_required"}
        if any(self._contains_line_break_or_nul(str(a)) for a in args):
            return {"ok": False, "reason": "docker_args_invalid"}

        subcommand = ""
        for token in args:
            raw = str(token).strip()
            if not raw:
                continue
            if raw.startswith("-"):
                continue
            subcommand = raw.lower()
            break
        if not subcommand:
            return {
                "ok": False,
                "reason": "docker_subcommand_required",
                "allowed_subcommands": self.docker_allowed_subcommands,
            }
        if self.docker_allowed_subcommands and subcommand not in self.docker_allowed_subcommands:
            return {
                "ok": False,
                "reason": "docker_subcommand_not_allowed",
                "subcommand": subcommand,
                "allowed_subcommands": self.docker_allowed_subcommands,
            }

        cmd = [self.docker_bin] + list(args)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except Exception as e:
            return {"ok": False, "reason": f"docker_exec_error:{e}"}

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        all_out = (stdout + ("\n" + stderr if stderr else "")).strip()
        truncated = len(all_out.encode("utf-8", errors="ignore")) > self.max_docker_output_bytes
        if truncated:
            all_out = all_out[: self.max_docker_output_bytes]

        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": all_out,
            "truncated": truncated,
            "cmd": cmd,
        }

    def read_journal(
        self,
        unit: Optional[str] = None,
        lines: int = 100,
        since: Optional[str] = None,
    ) -> Dict[str, object]:
        if not self.enabled:
            return {"ok": False, "reason": "system_executor_disabled"}

        line_count = max(1, min(int(lines), self.max_journal_lines))
        cmd = ["journalctl", "--no-pager", "-n", str(line_count)]
        if unit:
            cmd.extend(["-u", unit])
        if since:
            cmd.extend(["--since", since])

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except Exception as e:
            return {"ok": False, "reason": f"journal_exec_error:{e}"}

        out = (completed.stdout or "").strip()
        err = (completed.stderr or "").strip()
        if completed.returncode != 0:
            return {
                "ok": False,
                "reason": "journalctl_failed",
                "returncode": completed.returncode,
                "stderr": err[:2000],
            }
        return {
            "ok": True,
            "unit": unit,
            "lines": line_count,
            "output": out,
        }

    @staticmethod
    def _resolve_run_identity(
        run_as_user: str,
        run_as_uid: Optional[int],
        run_as_gid: Optional[int],
    ) -> Tuple[Optional[int], Optional[int]]:
        uid = None if run_as_uid is None else int(run_as_uid)
        gid = None if run_as_gid is None else int(run_as_gid)
        if uid is not None and gid is not None:
            return uid, gid

        try:
            pw = pwd.getpwnam(run_as_user)
            resolved_uid = int(pw.pw_uid)
            resolved_gid = int(pw.pw_gid)
            if gid is None:
                gid = resolved_gid
            if uid is None:
                uid = resolved_uid
            return uid, gid
        except Exception:
            pass

        if uid is None:
            uid = os.getuid()
        if gid is None:
            gid = os.getgid()
        return uid, gid

    @classmethod
    def _derive_gateway_identity(cls, peer_units: Set[str]) -> Optional[Tuple[str, str]]:
        for unit in sorted(peer_units):
            m = cls._UNIT_ROLE_RE.fullmatch(str(unit).strip())
            if m:
                return cls._canonical_mode_name(m.group(1)), m.group(2)
        return None

    @staticmethod
    def _is_under(root: Path, path: Path) -> bool:
        try:
            normalized_root = root.resolve(strict=False)
            normalized_path = path.resolve(strict=False)
        except Exception:
            return False
        if normalized_path == normalized_root:
            return True
        return str(normalized_path).startswith(str(normalized_root).rstrip("/") + "/")

    @staticmethod
    def _truncate_text(value: str, max_bytes: int) -> Tuple[str, bool]:
        raw = (value or "").encode("utf-8", errors="replace")
        if len(raw) <= max_bytes:
            return value or "", False
        cut = raw[:max_bytes]
        return cut.decode("utf-8", errors="replace"), True

    @staticmethod
    def _ensure_owned_dir(path: Path, uid: Optional[int], gid: Optional[int]) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if uid is not None or gid is not None:
            try:
                os.chown(path, uid if uid is not None else -1, gid if gid is not None else -1)
            except PermissionError:
                pass
        os.chmod(path, 0o700)

    def _sanitize_agent_env(self, request_env: dict, *, instance_id: str) -> Dict[str, str]:
        env: Dict[str, str] = {"PATH": self.agent_cli_path, "TMPDIR": "/tmp"}

        home_dir = Path(self.agent_cli_home_parent) / f"home-{instance_id}"
        codex_home = home_dir / ".codex"
        env["HOME"] = str(home_dir.resolve(strict=False))
        env["CODEX_HOME"] = str(codex_home.resolve(strict=False))

        if not isinstance(request_env, dict):
            return env

        reserved_keys = {"PATH", "TMPDIR", "HOME", "CODEX_HOME"}
        for raw_key, raw_value in request_env.items():
            key = str(raw_key).strip()
            if not key or key in reserved_keys:
                continue
            if not self._ENV_KEY_RE.fullmatch(key):
                continue
            if key.startswith(("LD_", "PYTHON")):
                continue
            if self.agent_cli_allowed_env_keys and key not in self.agent_cli_allowed_env_keys:
                continue
            env[key] = str(raw_value)
        return env

    def _build_agent_bwrap_command(
        self,
        *,
        exec_argv: List[str],
        cwd: Path,
        env: Dict[str, str],
        mode: str,
    ) -> List[str]:
        bwrap_cmd = [self.agent_cli_bwrap_command, "--die-with-parent", "--new-session"]
        strict_session_fs = mode == "session" and self.agent_cli_bwrap_session_workspace_only
        if self.agent_cli_bwrap_unshare_all:
            bwrap_cmd.extend(["--unshare-ipc", "--unshare-pid", "--unshare-uts"])
            if self.agent_cli_bwrap_unshare_user:
                bwrap_cmd.append("--unshare-user")
        if not self.agent_cli_bwrap_share_network:
            bwrap_cmd.append("--unshare-net")

        readonly_paths = self.agent_cli_bwrap_readonly_paths
        if strict_session_fs:
            readonly_paths = self.agent_cli_bwrap_session_runtime_ro_paths

        for path in readonly_paths:
            p = str(path).strip()
            if p:
                bwrap_cmd.extend(["--ro-bind-try", p, p])

        if strict_session_fs:
            # Hide host /etc from session callers; re-expose only minimum runtime files.
            bwrap_cmd.extend(["--tmpfs", "/etc"])
            for path in self.agent_cli_bwrap_session_etc_ro_paths:
                p = str(path).strip()
                if not p or not p.startswith("/etc"):
                    continue
                bwrap_cmd.extend(["--ro-bind-try", p, p])

        bwrap_cmd.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])

        sandbox_workspace = "/workspace"
        sandbox_home = "/sandbox-home"
        sandbox_codex_home = "/sandbox-home/.codex"

        home_raw = str(env.get("HOME", "")).strip()
        codex_raw = str(env.get("CODEX_HOME", "")).strip()
        home_path = Path(home_raw).resolve(strict=False) if home_raw else None
        codex_path = Path(codex_raw).resolve(strict=False) if codex_raw else None
        bwrap_cmd.extend(["--bind", str(cwd), sandbox_workspace])
        if home_path is not None:
            bwrap_cmd.extend(["--bind", str(home_path), sandbox_home])
        if codex_path is not None and (home_path is None or not self._is_under(home_path, codex_path)):
            bwrap_cmd.extend(["--bind", str(codex_path), sandbox_codex_home])

        for path in self.agent_cli_bwrap_mask_dirs:
            p = str(path).strip()
            if p:
                bwrap_cmd.extend(["--tmpfs", p])
        for path in self.agent_cli_bwrap_mask_files:
            p = str(path).strip()
            if p:
                bwrap_cmd.extend(["--ro-bind-try", "/dev/null", p])

        bwrap_cmd.extend(["--setenv", "PATH", self.agent_cli_path])
        bwrap_cmd.extend(["--setenv", "TMPDIR", "/tmp"])
        bwrap_cmd.extend(["--setenv", "HOME", sandbox_home])
        bwrap_cmd.extend(["--setenv", "CODEX_HOME", sandbox_codex_home])

        for key, value in env.items():
            if key in {"PATH", "TMPDIR", "HOME", "CODEX_HOME"}:
                continue
            bwrap_cmd.extend(["--setenv", str(key), str(value)])

        bwrap_cmd.extend(["--chdir", sandbox_workspace, "--", *exec_argv])
        return bwrap_cmd

    def _build_agent_exec_argv(
        self,
        *,
        command: str,
        args: List[str],
        run_as_root: bool = False,
    ) -> Tuple[Optional[List[str]], Optional[str]]:
        exec_argv = [command, *args]
        # Explicit sudo/root path: keep root identity, do not drop privileges.
        if os.geteuid() == 0 and bool(run_as_root):
            return exec_argv, None

        run_uid = self.agent_cli_run_uid
        run_gid = self.agent_cli_run_gid
        should_drop_uid = run_uid is not None and int(run_uid) != os.geteuid()
        should_drop_gid = run_gid is not None and int(run_gid) != os.getegid()
        if os.geteuid() != 0 or (not should_drop_uid and not should_drop_gid):
            return exec_argv, None

        setpriv_cmd = shutil.which("setpriv")
        if not setpriv_cmd:
            return None, "setpriv_not_found"

        prefix = [setpriv_cmd]
        if should_drop_gid and run_gid is not None:
            prefix.extend(["--regid", str(run_gid), "--clear-groups"])
        if should_drop_uid and run_uid is not None:
            prefix.extend(["--reuid", str(run_uid)])
        prefix.append("--")
        return [*prefix, *exec_argv], None

    def _normalize_agent_exec_request(
        self,
        action: dict,
        *,
        expected_mode: str,
        expected_instance_id: str,
    ) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
        if not isinstance(action, dict):
            return None, "action_not_object"
        if not self.agent_cli_enabled:
            return None, "agent_cli_disabled"

        request_mode = self._canonical_mode_name(action.get("mode", ""))
        if request_mode and request_mode != expected_mode:
            return None, "mode_mismatch"

        request_instance_id = str(action.get("instance_id", "")).strip()
        if request_instance_id and request_instance_id != expected_instance_id:
            return None, "instance_id_mismatch"

        agent = str(action.get("agent", "")).strip().lower()
        if not agent:
            return None, "agent_required"
        if self.agent_cli_allowed_agents and agent not in self.agent_cli_allowed_agents:
            return None, "agent_not_allowed"

        command = str(action.get("command", "")).strip()
        if not command:
            return None, "command_required"
        if "/" in command:
            return None, "command_must_be_basename"
        if self.agent_cli_allowed_commands and command not in self.agent_cli_allowed_commands:
            return None, "command_not_allowed"
        if shutil.which(command) is None:
            return None, "command_not_found"

        raw_args = action.get("args")
        if not isinstance(raw_args, list):
            return None, "args_not_list"
        if len(raw_args) > self.agent_cli_max_args:
            return None, "args_too_many"
        args = [str(v) for v in raw_args]
        # CLI prompts are often multiline; only reject NUL bytes.
        if any(self._contains_nul(a) for a in args):
            return None, "args_invalid"

        cwd_raw = str(action.get("cwd", "")).strip()
        if not cwd_raw:
            return None, "cwd_required"
        cwd = Path(self._normalize_path(cwd_raw))
        workspace_root = Path(self.agent_cli_workspace_parent) / expected_instance_id
        if not self._is_under(workspace_root, cwd):
            return None, "cwd_not_in_workspace"

        timeout_seconds = action.get("timeout_seconds", self.agent_cli_max_timeout_seconds)
        try:
            timeout_seconds = int(timeout_seconds)
        except Exception:
            timeout_seconds = self.agent_cli_max_timeout_seconds
        timeout_seconds = max(1, min(timeout_seconds, self.agent_cli_max_timeout_seconds))

        env = self._sanitize_agent_env(action.get("env"), instance_id=expected_instance_id)
        run_as_root = bool(action.get("run_as_root", False))
        if run_as_root and expected_mode != "system":
            return None, "root_exec_forbidden_in_mode"
        return {
            "agent": agent,
            "command": command,
            "args": args,
            "cwd": cwd.resolve(strict=False),
            "timeout_seconds": timeout_seconds,
            "env": env,
            "mode": expected_mode,
            "instance_id": expected_instance_id,
            "run_as_root": run_as_root,
        }, None

    def _prepare_agent_cli_exec(
        self,
        action: dict,
        *,
        peer_uid: Optional[int] = None,
        peer_units: Optional[Set[str]] = None,
    ) -> Tuple[Optional[Dict[str, object]], Optional[Dict[str, object]]]:
        if not self.enabled:
            return None, {"ok": False, "reason": "system_executor_disabled"}

        identity = self._derive_gateway_identity(set(peer_units or set()))
        if identity is None:
            return None, {"ok": False, "reason": "caller_identity_unknown"}
        caller_mode, caller_instance_id = identity

        normalized, error = self._normalize_agent_exec_request(
            action,
            expected_mode=caller_mode,
            expected_instance_id=caller_instance_id,
        )
        if error:
            return None, {"ok": False, "reason": error}
        assert normalized is not None

        command = str(normalized["command"])
        args = list(normalized["args"])
        cwd = Path(str(normalized["cwd"]))
        timeout_seconds = int(normalized["timeout_seconds"])
        env = dict(normalized["env"])
        run_as_root = bool(normalized.get("run_as_root", False))
        try:
            home_dir = Path(env.get("HOME", "")).resolve(strict=False)
            if str(home_dir):
                self._ensure_owned_dir(
                    home_dir,
                    None if run_as_root else self.agent_cli_run_uid,
                    None if run_as_root else self.agent_cli_run_gid,
                )
            codex_home = Path(env.get("CODEX_HOME", "")).resolve(strict=False)
            if str(codex_home):
                self._ensure_owned_dir(
                    codex_home,
                    None if run_as_root else self.agent_cli_run_uid,
                    None if run_as_root else self.agent_cli_run_gid,
                )
        except Exception as e:
            return None, {"ok": False, "reason": f"agent_cli_home_setup_failed:{e}"}

        exec_argv, exec_error = self._build_agent_exec_argv(
            command=command,
            args=args,
            run_as_root=run_as_root,
        )
        if exec_error:
            return None, {"ok": False, "reason": exec_error}
        assert exec_argv is not None

        run_cmd = exec_argv
        if run_as_root:
            run_cmd = exec_argv
        elif self.agent_cli_bwrap_enabled:
            run_cmd = self._build_agent_bwrap_command(
                exec_argv=exec_argv,
                cwd=cwd,
                env=env,
                mode=caller_mode,
            )
        elif self.agent_cli_bwrap_required:
            return None, {"ok": False, "reason": "bwrap_required_but_disabled"}

        return {
            "run_cmd": run_cmd,
            "timeout_seconds": timeout_seconds,
            "env": env,
            "mode": caller_mode,
            "instance_id": caller_instance_id,
            "peer_uid": peer_uid,
            "run_as_root": run_as_root,
        }, None

    @staticmethod
    def _stream_reader_thread(
        stream,
        stream_name: str,
        out_queue: "queue.Queue[Tuple[str, str, bytes]]",
    ) -> None:
        try:
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                if isinstance(chunk, str):
                    payload = chunk.encode("utf-8", errors="replace")
                else:
                    payload = bytes(chunk)
                out_queue.put(("chunk", stream_name, payload))
        except Exception as e:  # noqa: BLE001
            out_queue.put(("pipe_error", stream_name, str(e).encode("utf-8", errors="replace")))
        finally:
            out_queue.put(("eof", stream_name, b""))

    def agent_cli_exec(
        self,
        action: dict,
        *,
        peer_uid: Optional[int] = None,
        peer_units: Optional[Set[str]] = None,
    ) -> Dict[str, object]:
        prepared, prep_error = self._prepare_agent_cli_exec(
            action,
            peer_uid=peer_uid,
            peer_units=peer_units,
        )
        if prep_error:
            return prep_error
        assert prepared is not None

        run_cmd = list(prepared["run_cmd"])
        timeout_seconds = int(prepared["timeout_seconds"])
        env = dict(prepared["env"])

        try:
            completed = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            stdout_text = (e.stdout or "").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else str(e.stdout or "")
            stderr_text = (e.stderr or "").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr or "")
            stdout_text, out_truncated = self._truncate_text(stdout_text, self.agent_cli_max_output_bytes)
            stderr_text, err_truncated = self._truncate_text(stderr_text, self.agent_cli_max_output_bytes)
            return {
                "ok": False,
                "reason": "agent_cli_timeout",
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": out_truncated,
                "stderr_truncated": err_truncated,
                "mode": prepared["mode"],
                "instance_id": prepared["instance_id"],
                "peer_uid": prepared["peer_uid"],
                "run_as_root": prepared["run_as_root"],
            }
        except FileNotFoundError as e:
            return {"ok": False, "reason": f"agent_cli_exec_error:{e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"agent_cli_exec_error:{e}"}

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        stdout, out_truncated = self._truncate_text(stdout, self.agent_cli_max_output_bytes)
        stderr, err_truncated = self._truncate_text(stderr, self.agent_cli_max_output_bytes)
        return {
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "timed_out": False,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": out_truncated,
            "stderr_truncated": err_truncated,
            "mode": prepared["mode"],
            "instance_id": prepared["instance_id"],
            "peer_uid": prepared["peer_uid"],
            "run_as_root": prepared["run_as_root"],
        }

    def agent_cli_exec_stream(
        self,
        action: dict,
        *,
        peer_uid: Optional[int] = None,
        peer_units: Optional[Set[str]] = None,
    ):
        prepared, prep_error = self._prepare_agent_cli_exec(
            action,
            peer_uid=peer_uid,
            peer_units=peer_units,
        )
        if prep_error:
            payload = dict(prep_error)
            payload.setdefault("event", "done")
            yield payload
            return
        assert prepared is not None

        run_cmd = list(prepared["run_cmd"])
        timeout_seconds = int(prepared["timeout_seconds"])
        env = dict(prepared["env"])
        max_output = int(self.agent_cli_max_output_bytes)
        heartbeat_seconds = float(self.agent_cli_stream_heartbeat_seconds)
        poll_seconds = float(self.agent_cli_stream_queue_poll_seconds)

        try:
            proc = subprocess.Popen(
                run_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except Exception as e:  # noqa: BLE001
            yield {"event": "done", "ok": False, "reason": f"agent_cli_exec_error:{e}"}
            return

        if proc.stdout is None or proc.stderr is None:
            yield {"event": "done", "ok": False, "reason": "agent_cli_pipe_unavailable"}
            return

        events: "queue.Queue[Tuple[str, str, bytes]]" = queue.Queue()
        out_parts: List[str] = []
        err_parts: List[str] = []
        out_seen_bytes = 0
        err_seen_bytes = 0
        out_buffered_bytes = 0
        err_buffered_bytes = 0
        out_truncated = False
        err_truncated = False
        timed_out = False
        eof_streams: Set[str] = set()

        t_stdout = threading.Thread(
            target=self._stream_reader_thread,
            args=(proc.stdout, "stdout", events),
            daemon=True,
        )
        t_stderr = threading.Thread(
            target=self._stream_reader_thread,
            args=(proc.stderr, "stderr", events),
            daemon=True,
        )
        t_stdout.start()
        t_stderr.start()

        started = time.monotonic()
        last_heartbeat = started
        while True:
            now = time.monotonic()
            if now - started > timeout_seconds:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass

            try:
                kind, stream_name, payload = events.get(timeout=poll_seconds)
            except queue.Empty:
                kind, stream_name, payload = "", "", b""

            if kind == "chunk":
                text = payload.decode("utf-8", errors="replace")
                stream_limit = max_output
                if stream_name == "stderr":
                    err_seen_bytes += len(payload)
                    if err_seen_bytes > stream_limit:
                        err_truncated = True
                    if err_buffered_bytes < stream_limit:
                        keep = payload[: max(0, stream_limit - err_buffered_bytes)]
                        err_buffered_bytes += len(keep)
                        if keep:
                            err_parts.append(keep.decode("utf-8", errors="replace"))
                    if not err_truncated:
                        yield {"event": "chunk", "stream": "stderr", "data": text}
                else:
                    out_seen_bytes += len(payload)
                    if out_seen_bytes > stream_limit:
                        out_truncated = True
                    if out_buffered_bytes < stream_limit:
                        keep = payload[: max(0, stream_limit - out_buffered_bytes)]
                        out_buffered_bytes += len(keep)
                        if keep:
                            out_parts.append(keep.decode("utf-8", errors="replace"))
                    if not out_truncated:
                        yield {"event": "chunk", "stream": "stdout", "data": text}
            elif kind == "pipe_error":
                msg = payload.decode("utf-8", errors="replace")
                err_parts.append(f"[{stream_name}_read_error] {msg}")
                yield {"event": "chunk", "stream": "stderr", "data": f"[{stream_name}_read_error] {msg}\n"}
            elif kind == "eof":
                eof_streams.add(stream_name)

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_seconds:
                yield {"event": "heartbeat"}
                last_heartbeat = now

            if timed_out and proc.poll() is not None and len(eof_streams) >= 2 and events.empty():
                break
            if proc.poll() is not None and len(eof_streams) >= 2 and events.empty():
                break

        try:
            returncode = int(proc.wait(timeout=1.0))
        except Exception:
            returncode = -1

        ok = (not timed_out) and (returncode == 0)
        done = {
            "event": "done",
            "ok": ok,
            "returncode": returncode,
            "timed_out": timed_out,
            "stdout": "".join(out_parts),
            "stderr": "".join(err_parts),
            "stdout_truncated": out_truncated,
            "stderr_truncated": err_truncated,
            "mode": prepared["mode"],
            "instance_id": prepared["instance_id"],
            "peer_uid": prepared["peer_uid"],
            "run_as_root": prepared["run_as_root"],
        }
        if timed_out:
            done["reason"] = "agent_cli_timeout"
            done["timeout_seconds"] = timeout_seconds
        yield done
