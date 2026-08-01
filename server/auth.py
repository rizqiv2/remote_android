"""
auth.py — Authentication, JWT, CSRF, and rate-limiting logic.

Security layers implemented here:
  1. bcrypt password verification (constant-time, no timing attacks)
  2. JWT tokens stored in HttpOnly cookies (JS cannot steal them)
  3. CSRF double-submit token (separate cookie + request header)
  4. In-memory IP lockout after N failed attempts (anti-brute-force)
"""
import secrets
import time
import hmac
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, Header, HTTPException, Request, status

from .config import settings


# ─── Password ────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison — no timing oracle."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt (cost factor 12). Used in setup only."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


# ─── JWT ─────────────────────────────────────────────────────────────────────

def create_jwt() -> str:
    """Create a signed JWT with an expiry."""
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRE_HOURS),
        "sub": "remote_session",
        # Random jti prevents token cloning / replay (best-effort without a
        # server-side token store — for a full revocation list, use Redis)
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on any failure."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")


# ─── CSRF ─────────────────────────────────────────────────────────────────────

def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf(
    x_csrf_token: Optional[str] = Header(default=None),
    csrf_token: Optional[str] = Cookie(default=None),
) -> None:
    """
    Double-submit cookie CSRF validation.
    The browser sends the CSRF cookie automatically; the JS code also sends
    the token as X-CSRF-Token header. An attacker on another origin cannot
    read the cookie value (same-origin policy), so they cannot forge the header.
    """
    if not csrf_token or not x_csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing")
    # Use hmac.compare_digest to prevent timing attacks
    if not hmac.compare_digest(csrf_token, x_csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token invalid")


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

@dataclass
class _Bucket:
    attempts: int = 0
    locked_until: float = 0.0
    timestamps: list = field(default_factory=list)


class RateLimiter:
    """
    Per-IP brute-force protection.
    Allows up to MAX_ATTEMPTS failures in a sliding window.
    After that, the IP is locked for LOCKOUT_SECONDS.
    """

    def __init__(self):
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def _get_ip(self, request: Request) -> str:
        # Prefer X-Forwarded-For when behind a trusted reverse proxy/tunnel
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def check(self, request: Request) -> None:
        """
        Call before processing a login attempt.
        Raises HTTP 429 if the IP is locked out.
        """
        ip = self._get_ip(request)
        bucket = self._buckets[ip]
        now = time.monotonic()

        if bucket.locked_until > now:
            remaining = int(bucket.locked_until - now)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "too_many_attempts",
                    "retry_after": remaining,
                    "message": f"Too many failed attempts. Try again in {remaining} seconds.",
                },
            )

        # Slide the window: drop timestamps older than LOCKOUT_SECONDS
        cutoff = now - settings.RATE_LIMIT_LOCKOUT_SECONDS
        bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

    def record_failure(self, request: Request) -> None:
        """Call after a failed login attempt."""
        ip = self._get_ip(request)
        bucket = self._buckets[ip]
        now = time.monotonic()
        bucket.timestamps.append(now)

        if len(bucket.timestamps) >= settings.RATE_LIMIT_MAX_ATTEMPTS:
            bucket.locked_until = now + settings.RATE_LIMIT_LOCKOUT_SECONDS
            bucket.timestamps = []  # Reset so counter restarts after lockout

    def record_success(self, request: Request) -> None:
        """Call after a successful login — clears the failure counter."""
        ip = self._get_ip(request)
        self._buckets[ip] = _Bucket()


# Singleton — shared across all requests
rate_limiter = RateLimiter()


# ─── Auth Dependency ──────────────────────────────────────────────────────────

def require_auth(
    session: Optional[str] = Cookie(default=None),
) -> dict:
    """
    FastAPI dependency — validates the JWT session cookie.
    Use as: `Depends(require_auth)` on protected routes.
    """
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return decode_jwt(session)


def require_auth_and_csrf(
    session: Optional[str] = Cookie(default=None),
    x_csrf_token: Optional[str] = Header(default=None),
    csrf_token: Optional[str] = Cookie(default=None),
) -> dict:
    """
    Combined auth + CSRF check for state-changing endpoints.
    """
    claims = require_auth(session)
    validate_csrf(x_csrf_token, csrf_token)
    return claims
