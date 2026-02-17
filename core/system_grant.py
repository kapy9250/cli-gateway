"""Short-lived signed grant for privileged system actions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    s = (value or "").strip()
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def canonical_action(action_payload) -> str:
    """Canonicalize action payload for deterministic hashing/signing."""
    if isinstance(action_payload, str):
        return action_payload
    return json.dumps(action_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_hash(action_payload) -> str:
    return hashlib.sha256(canonical_action(action_payload).encode("utf-8")).hexdigest()


@dataclass
class GrantClaims:
    user_id: str
    action_hash: str
    nonce: str
    issued_at: int
    expires_at: int


class SystemGrantManager:
    """Issue and verify one-time short-lived action grants.

    Token format: compact JWS-like string with HS256 signature.
    """

    def __init__(self, secret: str, ttl_seconds: int = 60):
        self.secret = (secret or "").encode("utf-8")
        if not self.secret:
            raise ValueError("system grant secret is required")
        self.ttl_seconds = max(5, int(ttl_seconds))
        self._consumed_nonces: Dict[str, int] = {}
        self._nonce_lock = threading.Lock()

    def _cleanup(self, now: int) -> None:
        stale = [nonce for nonce, exp in self._consumed_nonces.items() if exp <= now]
        for nonce in stale:
            self._consumed_nonces.pop(nonce, None)

    def _sign(self, signing_input: bytes) -> str:
        sig = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        return _b64url_encode(sig)

    def issue(self, user_id: str, action_payload, now: Optional[int] = None) -> str:
        ts = int(time.time() if now is None else now)
        claims = {
            "uid": str(user_id),
            "act": action_hash(action_payload),
            "nonce": secrets.token_hex(12),
            "iat": ts,
            "exp": ts + self.ttl_seconds,
        }
        header = {"alg": "HS256", "typ": "SYSGRANT", "v": 1}

        enc_header = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        enc_claims = _b64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signing_input = f"{enc_header}.{enc_claims}".encode("ascii")
        signature = self._sign(signing_input)
        return f"{enc_header}.{enc_claims}.{signature}"

    def verify(
        self,
        token: str,
        user_id: str,
        action_payload,
        *,
        consume: bool = True,
        now: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[GrantClaims]]:
        ts = int(time.time() if now is None else now)
        with self._nonce_lock:
            self._cleanup(ts)

        parts = (token or "").split(".")
        if len(parts) != 3:
            return False, "token_malformed", None
        enc_header, enc_claims, signature = parts
        signing_input = f"{enc_header}.{enc_claims}".encode("ascii")
        expected = self._sign(signing_input)
        if not hmac.compare_digest(signature, expected):
            return False, "token_signature_invalid", None

        try:
            header = json.loads(_b64url_decode(enc_header).decode("utf-8"))
            claims = json.loads(_b64url_decode(enc_claims).decode("utf-8"))
        except Exception:
            return False, "token_decode_failed", None

        if str(header.get("typ")) != "SYSGRANT":
            return False, "token_type_invalid", None
        if str(header.get("alg")) != "HS256":
            return False, "token_alg_invalid", None

        uid = str(claims.get("uid", ""))
        act = str(claims.get("act", ""))
        nonce = str(claims.get("nonce", ""))
        iat = int(claims.get("iat", 0) or 0)
        exp = int(claims.get("exp", 0) or 0)
        if not uid or not act or not nonce or iat <= 0 or exp <= 0:
            return False, "token_claims_invalid", None
        if exp <= ts:
            return False, "token_expired", None
        if uid != str(user_id):
            return False, "token_user_mismatch", None
        if act != action_hash(action_payload):
            return False, "token_action_mismatch", None

        if consume:
            with self._nonce_lock:
                seen_exp = self._consumed_nonces.get(nonce)
                if seen_exp and seen_exp > ts:
                    return False, "token_replayed", None
                self._consumed_nonces[nonce] = exp

        grant = GrantClaims(
            user_id=uid,
            action_hash=act,
            nonce=nonce,
            issued_at=iat,
            expires_at=exp,
        )
        return True, "ok", grant
