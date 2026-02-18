"""Legacy /sys helpers.

The /sys command has been removed. This module intentionally keeps only
audit-redaction helpers for compatibility with existing tests/importers.
"""

from __future__ import annotations

import hashlib
import json
import time

AUDIT_REDACTED_FIELDS = {"text", "output", "stderr", "stdout", "content"}


def _redacted_value(value) -> dict:
    if value is None:
        return {"redacted": True, "bytes": 0}
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="replace")
    else:
        raw = str(value).encode("utf-8", errors="replace")
    return {
        "redacted": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _sanitize_for_audit(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if str(k).lower() in AUDIT_REDACTED_FIELDS:
                cleaned[k] = _redacted_value(v)
            else:
                cleaned[k] = _sanitize_for_audit(v)
        return cleaned
    if isinstance(obj, list):
        return [_sanitize_for_audit(v) for v in obj]
    return obj


def _audit(ctx, action: str, payload: dict, result: dict) -> None:
    logger = getattr(ctx, "audit_logger", None)
    if logger is None:
        return
    event = {
        "ts": time.time(),
        "channel": ctx.message.channel,
        "chat_id": ctx.message.chat_id,
        "user_id": ctx.user_id,
        "action": action,
        "payload": _sanitize_for_audit(payload),
        "result": _sanitize_for_audit(result),
    }
    logger.info(json.dumps(event, ensure_ascii=False, sort_keys=True))
