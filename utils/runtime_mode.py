"""Runtime mode helpers.

External names:
- user (least privilege, session sandbox)
- sys  (system operator mode)

Internal canonical names stay compatible with existing code:
- session
- system
"""

from __future__ import annotations


_ALIASES = {
    "user": "session",
    "session": "session",
    "sys": "system",
    "system": "system",
}


def normalize_runtime_mode(raw_mode: str | None, default: str = "session") -> str:
    value = str(raw_mode or "").strip().lower()
    if not value:
        return _ALIASES.get(str(default).strip().lower(), "session")
    return _ALIASES.get(value, _ALIASES.get(str(default).strip().lower(), "session"))


def to_external_mode(raw_mode: str | None) -> str:
    canonical = normalize_runtime_mode(raw_mode)
    if canonical == "system":
        return "sys"
    return "user"


def is_system_mode(raw_mode: str | None) -> bool:
    return normalize_runtime_mode(raw_mode) == "system"


def is_user_mode(raw_mode: str | None) -> bool:
    return normalize_runtime_mode(raw_mode) == "session"
