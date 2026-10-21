"""Session & CSRF token utilities.

Session bearer tokens contain 256 bits of CSPRNG entropy (`secrets.token_urlsafe(32)`),
are sent only inside an `HttpOnly` cookie, and are persisted server-side strictly as a
secret-keyed `HMAC-SHA256` digest (`token_hash`). This gives:
  1. Zero plaintext session tokens stored in the database.
  2. O(1) indexed lookup (`WHERE token_hash = :digest`) with zero cross-user scanning.
  3. Immediate global invalidation if `SESSION_SECRET` is rotated.
"""

import hashlib
import hmac
import secrets

from app.config import settings

TOKEN_BYTES = 32
CSRF_BYTES = 24


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_BYTES)
