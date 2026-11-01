"""A bounded, idempotent driver for serverless deployments.

Why this exists
---------------
The resident ``ProcessingWorker`` dispatcher is a thread pool that lives as
long as the process — exactly right for a container, exactly wrong for a
serverless Function that freezes between requests. On serverless the
deployment sets ``PROCESSING_WORKER_ENABLED=false`` and something else must
drive processing.

That something is the *tick*: one bounded pass that recovers stale jobs,
reclaims expired upload sessions, and processes a limited number of queued
jobs within a wall-clock budget. Ticks are safe to repeat, overlap, and
starve:

* **Claiming is exclusive.** Jobs move ``queued -> running`` inside a
  ``SELECT ... FOR UPDATE SKIP LOCKED`` transaction, so two overlapping
  ticks never process the same job.
* **Each job is idempotent** (generation-based segment replacement), and
  per-job failures are recorded with bounded retries and a terminal
  ``failed`` state — the document row *is* the dead-letter record, with a
  machine-readable ``error_code``.
* **The pass is bounded** by ``max_jobs`` and a deadline, so a Function
  invocation finishes well within the platform's execution limit and
  leaves the rest of the queue for the next tick.
* **One driver per deployment.** A tick refuses to run when the resident
  worker is enabled: the worker lease's single-writer reasoning stays
  intact (duplicate execution is prevented either way, but stale
  recovery and failure accounting are reasoned about for one driver).

Who calls ticks is a deployment decision: the frontend fires one after an
upload finalizes (self-driving), a scheduler (e.g. a GitHub Actions cron or
an uptime monitor) POSTs the token-authenticated internal endpoint, or an
operator runs one by hand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic

from sqlalchemy import select

from mafhum_api.db.base import Database
from mafhum_api.db.models import DocumentProcessingJob, JobStatus
from mafhum_api.services.documents.upload_sessions import UploadSessionService
from mafhum_api.services.documents.worker import ProcessingWorker

logger = logging.getLogger(__name__)

STOP_DRAINED = "drained"
STOP_MAX_JOBS = "max_jobs"
STOP_BUDGET = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of one bounded processing pass. Counters only — no user data."""

    recovered_stale_jobs: int
    expired_upload_sessions: int
    processed: int
    succeeded: int
    failed: int
    stopped_reason: str


class ProcessingTicker:
    """Composes the worker's per-job machinery into one bounded pass."""

    def __init__(
        self,
        database: Database,
        worker: ProcessingWorker,
        upload_sessions: UploadSessionService,
        *,
        max_jobs: int,
        budget_seconds: float,
    ) -> None:
        if max_jobs <= 0:
            raise ValueError("max_jobs must be positive")
        if budget_seconds <= 0:
            raise ValueError("budget_seconds must be positive")
        self._db = database
        self._worker = worker
        self._upload_sessions = upload_sessions
        self._max_jobs = max_jobs
        self._budget_seconds = budget_seconds

    @property
    def worker_is_running(self) -> bool:
        return bool(self._worker.is_running)

    def tick(self) -> TickResult:
        """Run one bounded pass. Safe to call repeatedly and concurrently."""
        deadline = monotonic() + self._budget_seconds

        with self._db.unit_of_work() as db:
            recovered = self._worker.recover_stale_jobs(db)
        with self._db.unit_of_work() as db:
            expired = self._upload_sessions.cleanup_expired(db)

        processed = succeeded = failed = 0
        stopped = STOP_DRAINED
        for _ in range(self._max_jobs):
            if monotonic() >= deadline:
                stopped = STOP_BUDGET
                break
            with self._db.unit_of_work() as db:
                job_id = self._worker.claim_next_job(db)
            if job_id is None:
                stopped = STOP_DRAINED
                break
            self._worker.process_job(job_id)
            processed += 1
            status = self._job_status(job_id)
            if status == JobStatus.FAILED.value:
                failed += 1
            elif status == JobStatus.SUCCEEDED.value:
                succeeded += 1
            # Any other terminal state (e.g. cancelled because the document
            # was deleted mid-tick) is neither success nor failure.
            if processed == self._max_jobs:
                stopped = STOP_MAX_JOBS

        if stopped == STOP_MAX_JOBS:
            # The pass hit its job limit; report accurately whether anything
            # remains queued, because callers use this to decide whether to
            # tick again.
            with self._db.unit_of_work() as db:
                remaining = db.scalar(
                    select(DocumentProcessingJob.id).where(
                        DocumentProcessingJob.status == JobStatus.QUEUED.value
                    )
                )
            if remaining is None:
                stopped = STOP_DRAINED

        result = TickResult(
            recovered_stale_jobs=recovered,
            expired_upload_sessions=expired,
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            stopped_reason=stopped,
        )
        logger.info(
            "processing tick complete",
            extra={
                "event": "processing_tick",
                "processed": result.processed,
                "recovered": result.recovered_stale_jobs,
                "expired_sessions": result.expired_upload_sessions,
                "stopped_reason": result.stopped_reason,
            },
        )
        return result

    def _job_status(self, job_id: object) -> str:
        """Read a finished job's persisted terminal state."""
        with self._db.unit_of_work() as db:
            job = db.get(DocumentProcessingJob, job_id)
            return job.status if job is not None else ""
