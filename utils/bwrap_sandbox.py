"""Bubblewrap sandbox helper for user-mode CLI subprocesses."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_RO_PATHS = [
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/etc",
    "/run",
    "/opt",
    "/data",
    "/var",
]

_DEFAULT_MASK_DIRS = [
    "/root",
    "/home",
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/cron.hourly",
    "/etc/cron.monthly",
    "/etc/cron.weekly",
    "/var/spool/cron",
    "/var/spool/cron/crontabs",
]

_DEFAULT_MASK_FILES = [
    "/etc/crontab",
]


@dataclass
class BwrapPolicy:
    enabled: bool
    required: bool
    command: str = "bwrap"
    share_network: bool = True
    unshare_all: bool = True
    readonly_paths: List[str] = field(default_factory=list)
    mask_dirs: List[str] = field(default_factory=list)
    mask_files: List[str] = field(default_factory=list)
    extra_writable_paths: List[str] = field(default_factory=list)


def _dedupe_paths(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _safe_resolve(path_value: str, cwd: Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _as_list(value) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


class BwrapSandbox:
    """Build and validate bubblewrap command wrappers."""

    def __init__(self, runtime_mode: str, sandbox_config: Optional[dict] = None):
        self.runtime_mode = str(runtime_mode or "session").strip().lower()
        cfg = sandbox_config if isinstance(sandbox_config, dict) else {}
        bcfg = cfg.get("bwrap")
        bcfg = bcfg if isinstance(bcfg, dict) else {}

        default_enabled = self.runtime_mode == "session"
        self.policy = BwrapPolicy(
            enabled=bool(bcfg.get("enabled", default_enabled)),
            required=bool(bcfg.get("required", False)),
            command=str(bcfg.get("command", "bwrap")).strip() or "bwrap",
            share_network=bool(bcfg.get("share_network", True)),
            unshare_all=bool(bcfg.get("unshare_all", True)),
            readonly_paths=_dedupe_paths(_DEFAULT_RO_PATHS + _as_list(bcfg.get("readonly_paths"))),
            mask_dirs=_dedupe_paths(_DEFAULT_MASK_DIRS + _as_list(bcfg.get("mask_dirs"))),
            mask_files=_dedupe_paths(_DEFAULT_MASK_FILES + _as_list(bcfg.get("mask_files"))),
            extra_writable_paths=_dedupe_paths(_as_list(bcfg.get("extra_writable_paths"))),
        )

        self._probe_lock = threading.Lock()
        self._probe_result: Optional[Tuple[bool, str]] = None
        self._warned_fallback = False

    def _probe_once(self) -> Tuple[bool, str]:
        if not self.policy.enabled:
            return True, "disabled"

        cmd_path = shutil.which(self.policy.command)
        if not cmd_path:
            return False, f"{self.policy.command} not found in PATH"

        try:
            # Minimal host probe to detect userns/AppArmor failures early.
            result = subprocess.run(
                [self.policy.command, "--ro-bind", "/", "/", "--", "/usr/bin/true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"probe failed: {exc}"

        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            return False, detail or f"probe failed with exit code {result.returncode}"
        return True, "ok"

    def _ensure_ready(self) -> Tuple[bool, str]:
        with self._probe_lock:
            if self._probe_result is None:
                self._probe_result = self._probe_once()
            return self._probe_result

    def _build_writable_paths(self, work_dir: Path, env: Dict[str, str]) -> Tuple[List[str], Dict[str, str]]:
        new_env = dict(env)
        cwd = work_dir
        writable = [str(work_dir)]

        for var in ("HOME", "CODEX_HOME"):
            raw = new_env.get(var)
            if not raw:
                continue
            resolved = _safe_resolve(raw, cwd)
            new_env[var] = str(resolved)
            writable.append(str(resolved))

        for raw in self.policy.extra_writable_paths:
            resolved = _safe_resolve(raw, cwd)
            writable.append(str(resolved))

        new_env.setdefault("TMPDIR", "/tmp")
        return _dedupe_paths(writable), new_env

    def wrap(
        self,
        command: str,
        args: Sequence[str],
        *,
        work_dir: Path,
        env: Dict[str, str],
    ) -> Tuple[str, List[str], Dict[str, str]]:
        """Wrap command with bwrap in user/session mode."""
        if self.runtime_mode != "session" or not self.policy.enabled:
            return command, list(args), env

        ok, reason = self._ensure_ready()
        if not ok:
            if self.policy.required:
                raise RuntimeError(f"bwrap unavailable: {reason}")
            if not self._warned_fallback:
                logger.warning("bwrap sandbox disabled (fallback to direct exec): %s", reason)
                self._warned_fallback = True
            return command, list(args), env

        writable_paths, wrapped_env = self._build_writable_paths(work_dir.resolve(), env)

        wrapped_args: List[str] = []
        wrapped_args.extend(["--die-with-parent", "--new-session"])
        if self.policy.unshare_all:
            wrapped_args.append("--unshare-all")
        if self.policy.share_network:
            wrapped_args.append("--share-net")

        for path in self.policy.readonly_paths:
            wrapped_args.extend(["--ro-bind-try", path, path])

        wrapped_args.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])

        for path in writable_paths:
            wrapped_args.extend(["--bind", path, path])

        for path in self.policy.mask_dirs:
            wrapped_args.extend(["--tmpfs", path])
        for path in self.policy.mask_files:
            wrapped_args.extend(["--ro-bind-try", "/dev/null", path])

        wrapped_args.extend(["--chdir", str(work_dir.resolve()), "--", command, *list(args)])
        return self.policy.command, wrapped_args, wrapped_env
