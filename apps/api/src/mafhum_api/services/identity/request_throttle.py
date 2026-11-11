"""Per-client throttle for unauthenticated, mail-sending endpoints.

Why this exists
---------------
``LoginThrottle`` protects credential guessing. It does **not** protect the
account-recovery endpoints, because those are unauthenticated by design: a user
who cannot sign in is exactly the user who calls them.

Without a dedicated limit, ``POST /auth/password-reset/request`` is an open
mail-sending endpoint. Anyone could:

* flood one address with reset mail (a nuisance that costs the owner money),
* enumerate addresses by timing or by watching which requests the mail server
  accepts,
* burn the transactional-email quota of the provider, which on a free tier takes
  password recovery down for everyone.

Design
------
* **Database-backed, like the login throttle**, for the same reason: a per-process
  counter multiplies the real limit by the number of API replicas. The account
  lifecycle endpoints run on serverless functions where that multiplication is
  unbounded, so an in-memory counter there would not be a limit at all.
* **Keyed by HMAC(client), not by address.** The throttle only compares keys for
  equality; storing raw addresses would turn this table into a log of who tried to
  recover which account from where.
* **Separate bucket per scope**, so exhausting the reset bucket does not stop a
  user from verifying their address.
* **Fails open on database errors, fail-closed on the limit.** A database outage
  already means recovery cannot work (the token cannot be stored); returning 500
  instead of 429 would not protect anything and would hide the real cause.
* **Cheap**: two indexed statements per request and a prune of the caller's own
  expired rows.
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
from mafhum_api.db.models import ThrottledAction

logger = logging.getLogger(__name__)


class RequestThrottle:
    """Counts requests per (scope, hashed client) in the database."""

    def __init__(
        self,
        database: Database,
        *,
        pepper: str,
        window_seconds: int,
        max_attempts: int,
    ) -> None:
        self._database = database
        self._pepper = pepper.encode("utf-8")
        self._window = timedelta(seconds=window_seconds)
        self._max = max_attempts

    def hash_key(self, raw_key: str) -> str:
        """Return the stored form of a client key (keyed, so it is not reversible)."""
        return hmac.new(self._pepper, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()

    def check(self, scope: str, raw_key: str) -> None:
        """Raise ``RateLimitedError`` when this client has spent its budget.

        Called before any work: a throttled caller must not reach the code that
        looks up an account or touches the mail provider.
        """
        key = self.hash_key(raw_key)
        cutoff = datetime.now(UTC) - self._window
        try:
            with self._database.session() as db:
                recent = db.scalar(
                    select(func.count(ThrottledAction.id)).where(
                        ThrottledAction.scope == scope,
                        ThrottledAction.client_key == key,
                        ThrottledAction.attempted_at >= cutoff,
                    )
                )
        except SQLAlchemyError:
            logger.warning("request throttle unavailable", extra={"event": "throttle_unavailable"})
            return
        if int(recent or 0) >= self._max:
            raise RateLimitedError(
                "طلبات كثيرة في وقت قصير. انتظر قليلًا ثم أعد المحاولة.",
            )

    def record(self, scope: str, raw_key: str) -> None:
        """Store one attempt and prune this client's rows outside the window."""
        key = self.hash_key(raw_key)
        cutoff = datetime.now(UTC) - self._window
        try:
            with self._database.session() as db:
                db.execute(
                    delete(ThrottledAction).where(
                        ThrottledAction.scope == scope,
                        ThrottledAction.client_key == key,
                        ThrottledAction.attempted_at < cutoff,
                    )
                )
                db.add(
                    ThrottledAction(
                        scope=scope,
                        client_key=key,
                        attempted_at=datetime.now(UTC),
                    )
                )
                db.commit()
        except SQLAlchemyError:
            logger.warning(
                "request throttle write failed", extra={"event": "throttle_write_failed"}
            )

    def prune(self, *, older_than_seconds: int) -> int:
        """Delete rows older than the given age; for a periodic job."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        with self._database.session() as db:
            removed = execute_rowcount(
                db, delete(ThrottledAction).where(ThrottledAction.attempted_at < cutoff)
            )
            db.commit()
            return removed
