"""Two-factor approval manager for system-level actions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urlencode


logger = logging.getLogger(__name__)


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


@dataclass
class TwoFactorEnrollment:
    user_id: str
    secret: str
    created_at: float
    expires_at: float


class TwoFactorManager:
    def __init__(
        self,
        enabled: bool = False,
        ttl_seconds: int = 300,
        valid_window: int = 1,
        period_seconds: int = 30,
        digits: int = 6,
        secrets_by_user: Optional[Dict[str, str]] = None,
        state_file: Optional[str] = None,
        enrollment_ttl_seconds: int = 600,
        issuer: str = "CLI Gateway",
    ):
        self.enabled = bool(enabled)
        self.ttl_seconds = int(ttl_seconds)
        self.valid_window = int(valid_window)
        self.period_seconds = int(period_seconds)
        self.digits = int(digits)
        self.secrets_by_user: Dict[str, str] = {
            str(k): str(v).strip()
            for k, v in (secrets_by_user or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self.state_file = Path(state_file) if state_file else None
        self.enrollment_ttl_seconds = max(60, int(enrollment_ttl_seconds))
        self.issuer = str(issuer or "CLI Gateway").strip() or "CLI Gateway"
        self._challenges: Dict[str, TwoFactorChallenge] = {}
        self._pending_enrollments: Dict[str, TwoFactorEnrollment] = {}
        self._load_state()

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
        stale_enrollments = [uid for uid, st in self._pending_enrollments.items() if st.expires_at <= ts]
        for uid in stale_enrollments:
            self._pending_enrollments.pop(uid, None)

    def build_totp_uri(self, secret: str, account_name: str, issuer: Optional[str] = None) -> str:
        account_value = str(account_name or "").strip()
        if not account_value:
            raise ValueError("account_name is required")
        issuer_value = str(issuer or self.issuer).strip() or self.issuer
        label = f"{issuer_value}:{account_value}"
        query = urlencode(
            {
                "secret": str(secret or "").strip(),
                "issuer": issuer_value,
                "algorithm": "SHA1",
                "digits": str(self.digits),
                "period": str(self.period_seconds),
            }
        )
        return f"otpauth://totp/{quote(label, safe='')}?{query}"

    def begin_enrollment(
        self,
        user_id: str,
        account_name: Optional[str] = None,
        issuer: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, object]:
        now = time.time()
        self._cleanup(now)
        uid = str(user_id)

        existing = self._pending_enrollments.get(uid)
        reused = False
        if existing and existing.expires_at > now and not force:
            enrollment = existing
            reused = True
        else:
            enrollment = TwoFactorEnrollment(
                user_id=uid,
                secret=self.generate_secret(),
                created_at=now,
                expires_at=now + self.enrollment_ttl_seconds,
            )
            self._pending_enrollments[uid] = enrollment

        account_value = str(account_name or uid).strip() or uid
        issuer_value = str(issuer or self.issuer).strip() or self.issuer
        otpauth_uri = self.build_totp_uri(enrollment.secret, account_value, issuer=issuer_value)
        return {
            "user_id": uid,
            "secret": enrollment.secret,
            "issuer": issuer_value,
            "account_name": account_value,
            "otpauth_uri": otpauth_uri,
            "created_at": enrollment.created_at,
            "expires_at": enrollment.expires_at,
            "reused": reused,
            "already_configured": bool(self.secrets_by_user.get(uid)),
        }

    def verify_enrollment(self, user_id: str, code: str) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "two_factor_disabled"

        now = time.time()
        self._cleanup(now)
        uid = str(user_id)
        enrollment = self._pending_enrollments.get(uid)
        if not enrollment:
            return False, "enrollment_not_found"
        if enrollment.expires_at <= now:
            self._pending_enrollments.pop(uid, None)
            return False, "enrollment_expired"
        try:
            ok = self._verify_totp(enrollment.secret, code, now)
        except Exception:
            return False, "totp_secret_invalid"
        if not ok:
            return False, "totp_code_invalid"

        self.secrets_by_user[uid] = enrollment.secret
        self._pending_enrollments.pop(uid, None)
        self._save_state()
        return True, "enrollment_verified"

    def cancel_enrollment(self, user_id: str) -> bool:
        self._cleanup()
        uid = str(user_id)
        if uid not in self._pending_enrollments:
            return False
        self._pending_enrollments.pop(uid, None)
        return True

    def enrollment_status(self, user_id: str) -> Dict[str, object]:
        now = time.time()
        self._cleanup(now)
        uid = str(user_id)
        pending = self._pending_enrollments.get(uid)
        return {
            "configured": bool(self.secrets_by_user.get(uid)),
            "pending": pending is not None and pending.expires_at > now,
            "pending_expires_at": pending.expires_at if pending else None,
            "pending_created_at": pending.created_at if pending else None,
            "issuer": self.issuer,
        }

    def _load_state(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            secrets_payload = raw.get("secrets", {}) if isinstance(raw, dict) else {}
            if not isinstance(secrets_payload, dict):
                logger.warning("Invalid two_factor state payload: secrets must be an object")
                return
            for user_id, secret in secrets_payload.items():
                uid = str(user_id).strip()
                value = str(secret).strip()
                if not uid or not value:
                    continue
                old = self.secrets_by_user.get(uid)
                if old and old != value:
                    logger.warning(
                        "Overriding configured two_factor secret from state file for user %s",
                        uid,
                    )
                self.secrets_by_user[uid] = value
            self._ensure_state_file_permissions()
        except Exception as e:
            logger.warning("Failed to load two_factor state from %s: %s", self.state_file, e)

    def _ensure_state_file_permissions(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            os.chmod(self.state_file, 0o600)
        except Exception as e:
            logger.warning("Failed to chmod two_factor state file %s: %s", self.state_file, e)

    def _save_state(self) -> None:
        if not self.state_file:
            return
        payload = {
            "version": 1,
            "updated_at": int(time.time()),
            "secrets": {uid: secret for uid, secret in sorted(self.secrets_by_user.items()) if secret},
        }
        tmp_path = None
        try:
            parent = self.state_file.parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".two_factor_state.",
                suffix=".json",
                dir=str(parent),
            )
            try:
                os.fchmod(fd, 0o600)
            except Exception:
                # Not all platforms/filesystems support fchmod.
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.state_file)
            self._ensure_state_file_permissions()
        except Exception as e:
            logger.warning("Failed to persist two_factor state to %s: %s", self.state_file, e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

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
