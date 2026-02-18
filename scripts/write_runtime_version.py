#!/usr/bin/env python3
"""Write deploy-time runtime version file for gateway process diagnostics."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.runtime_version import DEFAULT_VERSION_FILE


def _repo_root() -> Path:
    return REPO_ROOT


def _resolve_output(path_arg: str | None) -> Path:
    root = _repo_root()
    if not path_arg:
        return root / DEFAULT_VERSION_FILE
    path = Path(path_arg)
    if path.is_absolute():
        return path
    return root / path


def _git_commit_short(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(root),
        stderr=subprocess.STDOUT,
        text=True,
        timeout=2.0,
    ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write runtime version file")
    parser.add_argument("--output", default=None, help="Output path (default: repo/.runtime-version)")
    parser.add_argument("--value", default=None, help="Explicit version string (skip git detection)")
    parser.add_argument("--prefix", default="git:", help="Prefix for git commit version")
    args = parser.parse_args(argv)

    root = _repo_root()
    output = _resolve_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.value and str(args.value).strip():
        version = str(args.value).strip()
    else:
        try:
            commit = _git_commit_short(root)
        except Exception as e:  # noqa: BLE001
            print(f"failed to detect git commit: {e}", file=sys.stderr)
            return 1
        version = f"{args.prefix}{commit}" if args.prefix else commit

    output.write_text(f"{version}\n", encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
