"""Runtime version detection helpers."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def detect_runtime_version() -> str:
    """Detect runtime version string for operator-facing diagnostics."""
    pinned = str(os.getenv("CLI_GATEWAY_VERSION", "")).strip()
    if pinned:
        return pinned

    repo_root = Path(__file__).resolve().parents[1]
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
