"""Session & session-bound CSRF token utilities.

Security properties:
  1. Session bearer tokens have 256 bits of CSPRNG entropy (`secrets.token_urlsafe(32)`),
     are transmitted only in an `HttpOnly` cookie (`__Host-` prefixed on HTTPS
     production), and are persisted server-side strictly as a keyed `HMAC-SHA256`
     digest (`sessions.token_hash`).
  2. CSRF tokens combine a 192-bit random nonce with a keyed `HMAC-SHA256` tag
     bound to `(SESSION_SECRET, session_token)`: `"{nonce}.{hmac_hex}"`.
     Validating BOTH double-submit equality (`cookie == X-CSRF-Token`) AND the
     server HMAC tag defeats sibling-subdomain / MITM cookie-injection ("cookie
     tossing") attacks even when an attacker can write cookies into the victim's jar.
  3. Rotating `SESSION_SECRET` immediately invalidates every active session and
     every issued CSRF token across the entire deployment without DB mutation.
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


def _csrf_mac(session_token: str, nonce: str) -> str:
    msg = f"mafhum:csrf:{session_token}:{nonce}".encode()
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        msg,
        hashlib.sha256,
    ).hexdigest()


def new_csrf_token(session_token: str = "") -> str:
    nonce = secrets.token_urlsafe(CSRF_BYTES)
    return f"{nonce}.{_csrf_mac(session_token, nonce)}"


def verify_csrf_token(
    cookie_token: str, header_token: str, session_token: str = ""
) -> bool:
    if not cookie_token or not header_token:
        return False
    if not hmac.compare_digest(
        cookie_token.encode("utf-8"), header_token.encode("utf-8")
    ):
        return False
    parts = cookie_token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    nonce, provided_mac = parts
    expected_mac = _csrf_mac(session_token, nonce)
    return hmac.compare_digest(provided_mac, expected_mac)
