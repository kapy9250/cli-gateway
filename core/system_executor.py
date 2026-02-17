"""System executor with read and write operations for system-mode instances."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional


class SystemExecutor:
    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.max_read_bytes = max(1, int(cfg.get("max_read_bytes", 65536)))
        self.max_journal_lines = int(cfg.get("max_journal_lines", 300))
        self.max_docker_output_bytes = int(cfg.get("max_docker_output_bytes", 200000))
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

    @staticmethod
    def _normalize_path(path: str) -> str:
        p = Path(path).expanduser()
        try:
            return str(p.resolve(strict=False))
        except Exception:
            return str(p.absolute())

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

        target = Path(target_path)
        backup = Path(self._normalize_path(backup_path))
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
