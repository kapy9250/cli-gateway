"""Two-factor approval manager for system-level actions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class TwoFactorChallenge:
    challenge_id: str
    user_id: str
    action: str
    action_hash: str
    created_at: float
    expires_at: float
    approved: bool = False
    approved_at: Optional[float] = None


class TwoFactorManager:
    def __init__(
        self,
        enabled: bool = False,
        ttl_seconds: int = 300,
        valid_window: int = 1,
        period_seconds: int = 30,
        digits: int = 6,
        secrets_by_user: Optional[Dict[str, str]] = None,
    ):
        self.enabled = bool(enabled)
        self.ttl_seconds = int(ttl_seconds)
        self.valid_window = int(valid_window)
        self.period_seconds = int(period_seconds)
        self.digits = int(digits)
        self.secrets_by_user: Dict[str, str] = {
            str(k): str(v).strip() for k, v in (secrets_by_user or {}).items()
        }
        self._challenges: Dict[str, TwoFactorChallenge] = {}

    @staticmethod
    def generate_secret() -> str:
        """Generate a base32 secret suitable for Google Authenticator."""
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    @staticmethod
    def _normalize_b32(secret: str) -> str:
        s = "".join((secret or "").strip().split()).upper()
        if not s:
            raise ValueError("empty secret")
        pad = "=" * ((8 - len(s) % 8) % 8)
        return s + pad

    def _totp_code(self, secret: str, at_time: float) -> str:
        key = base64.b32decode(self._normalize_b32(secret), casefold=True)
        counter = int(at_time // self.period_seconds)
        msg = struct.pack(">Q", counter)
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code_int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10 ** self.digits)
        return str(code_int).zfill(self.digits)

    def _verify_totp(self, secret: str, code: str, now: float) -> bool:
        value = (code or "").strip()
        if not value.isdigit() or len(value) != self.digits:
            return False
        for delta in range(-self.valid_window, self.valid_window + 1):
            expected = self._totp_code(secret, now + (delta * self.period_seconds))
            if hmac.compare_digest(value, expected):
                return True
        return False

    @staticmethod
    def _canonical_action(action_payload) -> str:
        if isinstance(action_payload, str):
            return action_payload
        return json.dumps(action_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _hash_action(cls, action_payload) -> str:
        payload = cls._canonical_action(action_payload)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cleanup(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        stale = [cid for cid, ch in self._challenges.items() if ch.expires_at <= ts]
        for cid in stale:
            self._challenges.pop(cid, None)

    def create_challenge(self, user_id: str, action_payload) -> TwoFactorChallenge:
        now = time.time()
        self._cleanup(now)
        challenge_id = secrets.token_hex(8)
        action = self._canonical_action(action_payload)
        ch = TwoFactorChallenge(
            challenge_id=challenge_id,
            user_id=str(user_id),
            action=action,
            action_hash=self._hash_action(action_payload),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._challenges[challenge_id] = ch
        return ch

    def approve_challenge(
        self,
        challenge_id: str,
        user_id: str,
        code: str,
        action_payload=None,
    ) -> Tuple[bool, str]:
        now = time.time()
        self._cleanup(now)

        ch = self._challenges.get(str(challenge_id))
        if not ch:
            return False, "challenge_not_found"
        if ch.user_id != str(user_id):
            return False, "challenge_owner_mismatch"
        if ch.approved:
            return False, "challenge_already_approved"
        if ch.expires_at <= now:
            return False, "challenge_expired"
        payload = ch.action if action_payload is None else action_payload
        if ch.action_hash != self._hash_action(payload):
            return False, "action_hash_mismatch"
        if not self.enabled:
            return False, "two_factor_disabled"

        secret = self.secrets_by_user.get(str(user_id))
        if not secret:
            return False, "totp_secret_not_configured"
        try:
            ok = self._verify_totp(secret, code, now)
        except Exception:
            return False, "totp_secret_invalid"
        if not ok:
            return False, "totp_code_invalid"

        ch.approved = True
        ch.approved_at = now
        return True, "approved"

    def consume_approval(self, challenge_id: str, user_id: str, action_payload=None) -> Tuple[bool, str]:
        now = time.time()
        self._cleanup(now)
        ch = self._challenges.get(str(challenge_id))
        if not ch:
            return False, "challenge_not_found"
        if ch.user_id != str(user_id):
            return False, "challenge_owner_mismatch"
        if ch.expires_at <= now:
            self._challenges.pop(str(challenge_id), None)
            return False, "challenge_expired"
        if not ch.approved:
            return False, "challenge_not_approved"
        payload = ch.action if action_payload is None else action_payload
        if ch.action_hash != self._hash_action(payload):
            return False, "action_hash_mismatch"

        self._challenges.pop(str(challenge_id), None)
        return True, "approved"

    def status(self, challenge_id: str, user_id: str) -> Dict[str, object]:
        self._cleanup()
        ch = self._challenges.get(str(challenge_id))
        if not ch or ch.user_id != str(user_id):
            return {"exists": False}
        return {
            "exists": True,
            "challenge_id": ch.challenge_id,
            "action": ch.action,
            "approved": ch.approved,
            "created_at": ch.created_at,
            "expires_at": ch.expires_at,
            "approved_at": ch.approved_at,
        }
