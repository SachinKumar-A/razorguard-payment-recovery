"""Authentication for the endpoints that can influence money.

`/ingest` accepts the data the detector reasons over. Anyone who can post to it
can manufacture a degradation, and the control plane will faithfully respond by
moving traffic. That makes it a money-influencing endpoint even though it never
touches money directly, and it is not one to leave open.

Two modes, both configured by environment variable:

- **token** - a shared bearer token. Adequate behind a mesh or a VPC where the
  network already restricts who can reach the port.
- **hmac** - each request carries a timestamp and an HMAC-SHA256 of the body.
  Survives a replay (timestamps outside the window are refused) and does not put
  a long-lived secret on the wire.

If neither is configured the service refuses to start unless
`REVENUEGUARD_ALLOW_INSECURE=1` is set explicitly. Defaulting to open would mean
the safe configuration is the one you have to remember, and that is the wrong
way round.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Optional

#: Requests older or newer than this are refused, which bounds replay.
CLOCK_SKEW_SECONDS = 300


class AuthError(Exception):
    """Raised with a reason safe to return to the caller."""


@dataclass
class AuthConfig:
    mode: str            # "token" | "hmac" | "insecure"
    secret: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AuthConfig":
        token = os.environ.get("REVENUEGUARD_TOKEN", "").strip()
        hmac_key = os.environ.get("REVENUEGUARD_HMAC_KEY", "").strip()

        if hmac_key:
            return cls("hmac", hmac_key)
        if token:
            return cls("token", token)
        if os.environ.get("REVENUEGUARD_ALLOW_INSECURE") == "1":
            return cls("insecure")
        raise RuntimeError(
            "no credential configured. Set REVENUEGUARD_HMAC_KEY (preferred) "
            "or REVENUEGUARD_TOKEN. To run without authentication - only ever "
            "on a private network - set REVENUEGUARD_ALLOW_INSECURE=1.")

    @property
    def enforced(self) -> bool:
        return self.mode != "insecure"


def _constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The signature a client computes. Exposed so callers can implement it."""
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(timestamp.encode("utf-8"))
    mac.update(b".")
    mac.update(body)
    return mac.hexdigest()


def verify(config: AuthConfig, authorization: Optional[str],
           timestamp: Optional[str], signature: Optional[str],
           body: bytes, now: Optional[float] = None) -> None:
    """Raise AuthError unless the request is authentic."""
    if not config.enforced:
        return

    if config.mode == "token":
        if not authorization:
            raise AuthError("missing Authorization header")
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise AuthError("expected 'Authorization: Bearer <token>'")
        if not _constant_time_equal(presented.strip(), config.secret or ""):
            raise AuthError("invalid token")
        return

    # hmac
    if not timestamp or not signature:
        raise AuthError("missing X-RevenueGuard-Timestamp or "
                        "X-RevenueGuard-Signature")
    try:
        sent_at = float(timestamp)
    except ValueError:
        raise AuthError("timestamp is not a unix time")

    current = time.time() if now is None else now
    if abs(current - sent_at) > CLOCK_SKEW_SECONDS:
        # Both directions: a far-future timestamp is as suspicious as a stale
        # one, and refusing only old ones leaves an obvious hole.
        raise AuthError(
            f"timestamp outside the {CLOCK_SKEW_SECONDS}s window")

    expected = sign(config.secret or "", timestamp, body)
    if not _constant_time_equal(signature.strip(), expected):
        raise AuthError("signature does not match")
