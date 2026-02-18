"""Runtime version detection helpers."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

DEFAULT_VERSION_FILE = ".runtime-version"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _version_file_path() -> Path:
    custom = str(os.getenv("CLI_GATEWAY_VERSION_FILE", "")).strip()
    if custom:
        return Path(custom)
    return _repo_root() / DEFAULT_VERSION_FILE


@lru_cache(maxsize=1)
def detect_runtime_version() -> str:
    """Detect runtime version string for operator-facing diagnostics."""
    pinned = str(os.getenv("CLI_GATEWAY_VERSION", "")).strip()
    if pinned:
        return pinned

    version_file = _version_file_path()
    try:
        file_version = version_file.read_text(encoding="utf-8").strip()
        if file_version:
            return file_version
    except Exception:
        pass

    repo_root = _repo_root()
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
        ).strip()
        if commit:
            return f"git:{commit}"
    except Exception:
        pass
    return "unknown"
