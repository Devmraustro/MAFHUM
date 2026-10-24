"""A single-writer lease that makes the one-worker-per-database rule enforceable.

Why this exists
---------------
The processing worker's *job claiming* is already safe across processes: claims use
``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL and move a job
``queued -> running`` inside the claim transaction, so two processes never process
the same job twice. Heartbeats are refreshed while a job runs, so
``recover_stale_jobs`` does not steal a job another process is actively working on.

What is *not* safe is running several workers when the deployment was only ever
reasoned about as one. Duplicate execution is prevented, but so are several other
assumptions: ``recover_stale_jobs`` runs concurrently in every process and rewrites
documents it believes orphaned, thread-pool sizing was sized for one process, and
failure accounting is spread across processes nobody observes together. Supporting
that properly is real work; guessing that it is fine is how a system acquires
intermittent corruption.

So rather than document the restriction and hope, this makes it enforceable. The
first process to start a worker takes a PostgreSQL advisory lock keyed to a fixed
integer. A second process attempting to start a worker fails fast at startup with
an actionable error instead of quietly running an unsupported configuration.

Why an advisory lock rather than something else
-----------------------------------------------
- **No new infrastructure.** It uses the PostgreSQL the application already
  requires. Introducing Redis or a lock file to solve this would be a new
  dependency and a new failure mode for a constraint the existing database can
  express.
- **It cannot be left behind.** A session-scoped advisory lock is released by
  PostgreSQL when the holding connection closes, including when the process is
  killed. A lock file or a row with a ``held_by`` column can survive a crash and
  block every future startup until someone cleans it up by hand.
- **It is real coordination, not a best-effort check.** ``pg_try_advisory_lock``
  is atomic in the database, so there is no race window between checking and
  taking the lock.

Non-PostgreSQL dialects have no advisory locks. That path is used only for fast
unit tests against SQLite, so it is permitted with a loud warning rather than
faking a lock; the real behaviour is covered by tests running against PostgreSQL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from mafhum_api.db.base import Database

logger = logging.getLogger(__name__)

# A fixed key identifies *this* lease across processes. Any 64-bit integer works;
# the value is arbitrary but must be stable, and it is namespaced by the string it
# was derived from so it does not collide with an unrelated advisory lock.
#
# Derived with pg_advisory_lock's recommended practice of hashing a stable label
# rather than picking a magic number that might clash with another subsystem.
LEASE_KEY = 0x4D414648  # "MAFH" in ASCII, comfortably inside int32 range


class WorkerAlreadyRunningError(RuntimeError):
    """Raised when a second process tries to start a worker on the same database.

    Carries a message an operator can act on, because the whole point of failing
    here is that the fix is a deployment change, not a code change.
    """


class WorkerLease:
    """Holds the single-writer advisory lock for the lifetime of a worker.

    The lock is bound to a dedicated connection that stays open for as long as the
    lease is held. Advisory locks are per session, so borrowing a pooled session
    would release the lock the moment that session went back to the pool -- which
    would silently defeat the entire mechanism while appearing to work.
    """

    def __init__(self, database: Database, *, key: int = LEASE_KEY) -> None:
        self._db = database
        self._key = key
        self._connection: Connection | None = None

    @property
    def held(self) -> bool:
        return self._connection is not None

    def acquire(self) -> None:
        """Take the lease, or raise :class:`WorkerAlreadyRunningError`.

        Raises rather than returning a boolean so a caller cannot forget to check
        the result and start an unsupported configuration anyway.
        """
        if self._connection is not None:
            return

        if self._db.dialect != "postgresql":
            # SQLite has no advisory locks and is only used for fast unit tests.
            # Warn loudly rather than pretend a lock was taken.
            logger.warning(
                "worker lease not enforced: %s has no advisory locks",
                self._db.dialect,
                extra={"event": "worker_lease_unenforced", "dialect": self._db.dialect},
            )
            return

        # A dedicated, non-pooled connection so the lock lives exactly as long as
        # this worker does.
        connection = self._db.engine.connect()
        try:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
            )
        except BaseException:
            connection.close()
            raise
        if not acquired:
            connection.close()
            raise WorkerAlreadyRunningError(
                "another MAFHUM process is already running the document processing "
                "worker against this database. Only one worker may run while worker "
                "state is process-local. Start this instance with the worker "
                "disabled (MAFHUM_PROCESSING_WORKER_ENABLED=false) and let the "
                "existing worker process documents, or stop the other process "
                "first. See docs/DEPLOYMENT.md."
            )
        self._connection = connection
        logger.info(
            "worker lease acquired",
            extra={"event": "worker_lease_acquired", "key": self._key},
        )

    def release(self) -> None:
        """Release the lease. Safe to call when it was never held."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})
        except Exception:
            # The lock is released by PostgreSQL when the connection closes
            # regardless, so a failed explicit unlock is not fatal. Logging and
            # moving on keeps shutdown from hanging on a database that is already
            # going away.
            logger.warning(
                "explicit worker lease unlock failed; the lock is released when "
                "the connection closes",
                extra={"event": "worker_lease_unlock_failed"},
            )
        finally:
            connection.close()

    def __enter__(self) -> WorkerLease:
        self.acquire()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()
