"""In-memory sliding-window rate limiter with keyed account digests.

Security properties:
- Normalized account identifiers are never stored in cleartext inside limiter
  buckets; `account_limiter_key()` computes a secret-keyed `HMAC-SHA256` digest
  over NFKC-normalized lowercase email strings.
- Expired buckets are pruned periodically so attacker-supplied distinct keys
  cannot grow process memory without bound.
- Single-process scope; swap for a shared Redis store behind the same `check()`
  contract when scaling horizontally across multiple workers.
"""

import hashlib
import hmac
import time
import unicodedata
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

PRUNE_EVERY_CHECKS = 256


def normalize_account_identifier(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().lower()


def account_limiter_key(email: str, secret: str) -> str:
    """Return a keyed HMAC-SHA256 bucket key without retaining raw email PII."""
    canonical = normalize_account_identifier(email)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"mafhum:auth:acct:{canonical}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"acct:{digest}"


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._checks_since_prune = 0
        self._lock = Lock()

    def _prune_expired_unlocked(self, cutoff: float) -> None:
        stale_keys = [
            k for k, timestamps in self._hits.items() if not timestamps or timestamps[-1] <= cutoff
        ]
        for k in stale_keys:
            del self._hits[k]

    def keys_snapshot(self) -> list[str]:
        """Return active bucket keys (used in security tests to verify no raw PII)."""
        with self._lock:
            return list(self._hits.keys())

    def check(
        self,
        key: str,
        max_attempts: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        """Raise HTTP 429 when `key` exceeds allowed attempts within the window."""
        limit = max_attempts if max_attempts is not None else self.max_attempts
        window = window_seconds if window_seconds is not None else self.window_seconds
        now = time.monotonic()
        with self._lock:
            window_start = now - window
            self._checks_since_prune += 1
            if self._checks_since_prune >= PRUNE_EVERY_CHECKS:
                self._prune_expired_unlocked(window_start)
                self._checks_since_prune = 0

            hits = [t for t in self._hits.get(key, ()) if t > window_start]
            if len(hits) >= limit:
                retry_after = max(int(window - (now - hits[0])) + 1, 1)
                self._hits[key] = hits
                raise HTTPException(
                    status_code=429,
                    detail="Too many authentication attempts. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
            self._hits[key] = hits

    def reset(self) -> None:
        """Clear recorded hits (used for deterministic test isolation)."""
        with self._lock:
            self._hits.clear()
            self._checks_since_prune = 0
