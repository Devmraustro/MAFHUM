"""In-memory sliding-window rate limiter for authentication endpoints.

Dependency-free, thread-safe, deterministic, and directly testable. Designed
for the Phase 1/2 single-process modular monolith; multi-process deployments
can swap in a shared store behind the same interface.
"""

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str, max_attempts: int | None = None, window_seconds: int | None = None) -> None:
        """Raise HTTP 429 when `key` exceeds allowed attempts within the window."""
        limit = max_attempts if max_attempts is not None else self.max_attempts
        window = window_seconds if window_seconds is not None else self.window_seconds
        now = time.monotonic()
        with self._lock:
            window_start = now - window
            hits = [t for t in self._hits[key] if t > window_start]
            if len(hits) >= limit:
                retry_after = max(int(window - (now - hits[0])) + 1, 1)
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
