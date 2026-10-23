"""Database-backed login throttle.

Replaces a module-level dictionary that counted attempts per process. The
difference matters: behind a load balancer with N replicas, a per-process counter
lets an attacker make roughly N times the configured number of attempts, so the
limit does not mean what it says. Counting in the database makes the configured
limit the real limit, no matter how many API processes serve traffic.

Two properties are deliberate:

* **It commits independently of the request transaction.** A failed login raises,
  and the request's session is discarded. If the attempt were recorded there it
  would vanish, and the throttle would never accumulate against the attempts that
  matter most -- the unsuccessful ones.
* **It prunes as it writes.** Recording an attempt deletes that key's rows outside
  the window, so the table stays bounded instead of growing by one row per
  address the service ever sees. The dictionary it replaced had no eviction at
  all and leaked memory for the life of the process.

The client key is HMACed before storage. The throttle only ever compares keys for
equality, so raw client addresses do not need to be kept, and are not.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from mafhum_api.core.errors import RateLimitedError
from mafhum_api.db.base import Database, execute_rowcount
from mafhum_api.db.models import LoginAttempt

logger = logging.getLogger(__name__)


class LoginThrottle:
    """Counts login attempts per client key, in the database."""

    def __init__(
        self,
        database: Database,
        *,
        window_seconds: int,
        max_attempts: int,
        pepper: str,
    ) -> None:
        self._database = database
        self._window = timedelta(seconds=window_seconds)
        self._max = max_attempts
        self._pepper = pepper.encode("utf-8")

    def hash_key(self, raw_key: str) -> str:
        """Return the stored form of a client key.

        Keyed with the application secret so the stored value cannot be reversed
        to an address by someone reading the table, and so the same address maps
        to the same value in every process.
        """
        return hmac.new(self._pepper, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()

    def check(self, raw_key: str) -> None:
        """Raise ``RateLimitedError`` once the attempt budget is exhausted.

        Checked *before* authentication so a blocked client does not get the
        cheaper "wrong password" answer indefinitely, and does not get to keep
        exercising the password hash.
        """
        key = self.hash_key(raw_key)
        cutoff = datetime.now(UTC) - self._window
        try:
            with self._database.session() as db:
                recent = db.scalar(
                    select(func.count(LoginAttempt.id)).where(
                        LoginAttempt.client_key == key,
                        LoginAttempt.attempted_at >= cutoff,
                    )
                )
        except SQLAlchemyError:
            # Failing closed here would lock every user out whenever the database
            # is briefly unavailable, and login is already impossible in that
            # state -- authentication needs the same database. Record the outage
            # and let the attempt proceed to the error it would hit anyway.
            logger.warning("login throttle unavailable", extra={"event": "throttle_unavailable"})
            return
        if int(recent or 0) >= self._max:
            raise RateLimitedError("محاولات تسجيل دخول كثيرة. انتظر قليلًا ثم أعد المحاولة.")

    def record(self, raw_key: str, *, succeeded: bool) -> None:
        """Store one attempt and prune that key's expired rows."""
        key = self.hash_key(raw_key)
        cutoff = datetime.now(UTC) - self._window
        try:
            with self._database.session() as db:
                db.execute(
                    delete(LoginAttempt).where(
                        LoginAttempt.client_key == key,
                        LoginAttempt.attempted_at < cutoff,
                    )
                )
                db.add(
                    LoginAttempt(
                        client_key=key,
                        attempted_at=datetime.now(UTC),
                        succeeded=succeeded,
                    )
                )
                db.commit()
        except SQLAlchemyError:
            # A throttle write must never turn a working login into a 500.
            logger.warning("login throttle write failed", extra={"event": "throttle_write_failed"})

    def prune(self, *, older_than_seconds: int) -> int:
        """Delete attempts older than the given age. Returns rows removed.

        Recording an attempt prunes that key's own history, which bounds the table
        under normal traffic. This exists for the case that pruning does not
        cover: keys that stopped attempting, whose rows would otherwise sit until
        the next attempt from that key. Intended for a periodic job or an
        operator, not for the request path.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        with self._database.session() as db:
            removed = execute_rowcount(
                db, delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff)
            )
            db.commit()
            return removed
