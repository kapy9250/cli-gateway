from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_unit_has_path_and_proc_isolation_defaults():
    unit = (ROOT / "systemd" / "cli-gateway-session@.service").read_text(encoding="utf-8")

    assert "UMask=0077" in unit
    assert "ProtectProc=invisible" in unit
    assert "ProcSubset=pid" in unit
    assert "InaccessiblePaths=/root" in unit
    assert "/etc/cron.d" in unit
    assert "/var/spool/cron" in unit
