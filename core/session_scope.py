"""Helpers for deriving session scope IDs and workspace directory names."""

from __future__ import annotations

import re

from channels.base import IncomingMessage

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return _SAFE_SEGMENT_RE.sub("_", text)


def build_scope_id(message: IncomingMessage) -> str:
    """Build a stable scope key for active-session routing."""
    channel = str(message.channel or "").strip().lower() or "unknown"
    if bool(message.is_private):
        return f"{channel}:dm:{message.user_id}"
    return f"{channel}:chat:{message.chat_id}"


def build_scope_workspace_dir(message: IncomingMessage) -> str:
    """Build per-scope workspace subdirectory name."""
    channel = _safe_segment(str(message.channel or "").strip().lower() or "unknown")
    if bool(message.is_private):
        return f"{channel}_user_{_safe_segment(message.user_id)}"
    return f"{channel}_{_safe_segment(message.chat_id)}"
