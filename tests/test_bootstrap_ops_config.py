import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "bootstrap_ops_config.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_ops_config", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"failed to load module spec from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

_build_parser = MODULE._build_parser
_write_yaml = MODULE._write_yaml


def test_parser_default_channel_profile_is_telegram_only():
    parser = _build_parser()
    args = parser.parse_args(["--source-config", "base.yaml", "--output", "ops.yaml"])
    assert args.channel_profile == "telegram-only"


def test_write_yaml_sets_strict_file_mode(tmp_path: Path):
    output = tmp_path / "ops.yaml"
    _write_yaml(output, {"auth": {"system_admin_users": ["1"]}})

    mode = output.stat().st_mode & 0o777
    assert mode == 0o600
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == {"auth": {"system_admin_users": ["1"]}}


def test_write_yaml_rewrites_existing_file_with_strict_mode(tmp_path: Path):
    output = tmp_path / "ops.yaml"
    output.write_text("channels: {}\n", encoding="utf-8")
    output.chmod(0o644)

    _write_yaml(output, {"channels": {"telegram": {"enabled": True}}})

    mode = output.stat().st_mode & 0o777
    assert mode == 0o600
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == {"channels": {"telegram": {"enabled": True}}}
