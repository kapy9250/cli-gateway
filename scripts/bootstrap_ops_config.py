#!/usr/bin/env python3
"""Generate a full system-mode ops config from an existing gateway config."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import yaml

# Allow running the script from any current working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.ops_config import merge_ops_config


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def _write_yaml(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, mode)
        if path.exists():
            current = path.stat()
            try:
                os.chown(tmp_path, current.st_uid, current.st_gid)
            except PermissionError:
                # Best effort; ownership may require elevated privileges.
                pass
        os.replace(tmp_path, path)
        os.chmod(path, mode)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bootstrap CLI Gateway ops config")
    p.add_argument("--source-config", required=True, help="Base gateway config path")
    p.add_argument(
        "--privileged-config",
        default=None,
        help="Optional minimal privileged config; system_ops/system_service from this file override source config",
    )
    p.add_argument("--output", required=True, help="Output config path")
    p.add_argument("--instance-id", default="ops-a", help="Ops instance id")
    p.add_argument("--health-host", default="127.0.0.1", help="Health endpoint host")
    p.add_argument("--health-port", type=int, default=18810, help="Health endpoint port")
    p.add_argument(
        "--channel-profile",
        choices=["keep", "telegram-only"],
        default="telegram-only",
        help="Channel enablement profile for ops instance",
    )
    p.add_argument("--no-backup", action="store_true", help="Do not create backup if output file exists")
    p.add_argument("--disable-two-factor", action="store_true", help="Do not force enable two_factor")
    p.add_argument(
        "--no-generate-totp-secrets",
        action="store_true",
        help="Do not generate missing TOTP secrets for system_admin_users",
    )
    p.add_argument("--print-otpauth", action="store_true", help="Print otpauth:// URIs for system_admin_users")
    return p


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def main() -> int:
    args = _build_parser().parse_args()

    source_path = Path(args.source_config).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    privileged_path = Path(args.privileged_config).expanduser().resolve() if args.privileged_config else None

    if not source_path.exists():
        raise FileNotFoundError(f"source config not found: {source_path}")
    if privileged_path and not privileged_path.exists():
        raise FileNotFoundError(f"privileged config not found: {privileged_path}")

    base_cfg = _load_yaml(source_path)
    privileged_cfg = _load_yaml(privileged_path) if privileged_path else None

    merged, meta = merge_ops_config(
        base_cfg,
        privileged_cfg,
        instance_id=args.instance_id,
        health_host=args.health_host,
        health_port=args.health_port,
        channel_profile=args.channel_profile,
        enable_two_factor=not args.disable_two_factor,
        generate_missing_totp=not args.no_generate_totp_secrets,
    )

    backup_path = None
    if not args.no_backup:
        backup_path = _backup_file(output_path)

    _write_yaml(output_path, merged)

    print(f"WROTE {output_path}")
    if backup_path:
        print(f"BACKUP {backup_path}")
    print("SYSTEM_ADMIN_USERS", ",".join(meta.get("system_admin_users", [])))
    print("GENERATED_SECRET_USERS", ",".join(meta.get("generated_secret_users", [])))
    print("CHANNEL_PROFILE", meta.get("channel_profile", "keep"))

    if args.print_otpauth:
        two_factor = merged.get("two_factor", {}) if isinstance(merged.get("two_factor"), dict) else {}
        secrets_by_user = two_factor.get("secrets", {}) if isinstance(two_factor.get("secrets"), dict) else {}
        digits = int(two_factor.get("digits", 6))
        period = int(two_factor.get("period_seconds", 30))
        issuer = f"CLI-Gateway-{args.instance_id}"
        for user_id, secret in secrets_by_user.items():
            label = f"{issuer}:{user_id}"
            uri = (
                f"otpauth://totp/{urllib.parse.quote(label)}"
                f"?secret={secret}"
                f"&issuer={urllib.parse.quote(issuer)}"
                f"&digits={digits}&period={period}"
            )
            print(f"OTP {user_id} {secret}")
            print(f"OTP_URI {uri}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
