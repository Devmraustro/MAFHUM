"""Credential, session and CSRF primitives.

Deliberately implemented on the standard library (``hashlib.scrypt``,
``secrets``, ``hmac``) so the authentication core carries no extra native
dependency. Session tokens are opaque random strings; only a SHA-256 digest is
persisted, so a database leak does not hand out usable sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
_FORMAT_VERSION = "scrypt"

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


class PasswordTooShortError(ValueError):
    pass


def hash_password(password: str) -> str:
    """Return a self-describing ``scrypt$...`` password hash.

    Raises ``PasswordTooShortError`` for weak inputs. Length is capped to bound
    the cost of hashing attacker-supplied strings.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError("password too short")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordTooShortError("password too long")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"{_FORMAT_VERSION}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of ``password`` against a stored hash."""
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, digest_hex = stored.split("$")
        if scheme != _FORMAT_VERSION:
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected) or _SCRYPT_DKLEN,
    )
    return hmac.compare_digest(candidate, expected)


def generate_session_token() -> str:
    """Return a fresh high-entropy session token."""
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    """Return the value persisted for a session token (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided, expected)


@dataclass(frozen=True, slots=True)
class OriginVerdict:
    allowed: bool
    reason: str


def _origin_of(value: str) -> str | None:
    """Reduce a URL or origin string to its ``scheme://host[:port]`` form.

    Origin headers already carry just the origin, while Referer headers carry a
    full URL including a path. Both must be reduced the same way before they can
    be compared against the allowlist; comparing a full Referer URL directly
    would never match.
    """
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/").lower()


def verify_origin(
    origin: str | None,
    referer: str | None,
    allowed_origins: list[str],
) -> OriginVerdict:
    """Validate the Origin/Referer of a cookie-authenticated state change.

    A missing Origin on a same-origin form post falls back to Referer. Requests
    with neither are rejected because a browser-driven mutation must carry one.
    Comparison is on the parsed origin, never on substring matching, so
    ``https://mafhum.example.evil`` cannot pass as ``https://mafhum.example``.
    """
    candidates = [value for value in (origin, referer) if value]
    if not candidates:
        return OriginVerdict(False, "missing_origin")
    allowed = {parsed for value in allowed_origins if (parsed := _origin_of(value)) is not None}
    for value in candidates:
        parsed = _origin_of(value)
        if parsed is not None and parsed in allowed:
            return OriginVerdict(True, "origin_allowed")
    return OriginVerdict(False, "origin_not_allowed")
