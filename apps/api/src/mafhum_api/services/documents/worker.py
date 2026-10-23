"""Bounded document-processing worker.

Execution model
---------------
A database-backed queue plus a small thread pool inside the API process. No
external broker is required, which keeps local development free and simple.

* **Claiming** — ``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL, so two
  workers never process the same job. On backends without row locking (SQLite,
  used only for fast unit tests) an atomic conditional ``UPDATE`` provides the
  same claim-or-skip semantics.
* **Duplicate prevention** — a job row is claimed by moving it
  ``queued -> running`` inside the claim transaction. ``UNIQUE(document_id,
  generation)`` means a retried run can never collide with a previous one.
* **Stale recovery** — a running job whose heartbeat has not advanced for
  ``stale_after_seconds`` is requeued. Documents left in ``processing`` with no
  live job are returned to ``pending``.
* **Timeout** — the parse runs under a wall-clock deadline. A job exceeding it is
  marked failed and its slot is released.

Known limitation (documented, not hidden): a Python thread blocked inside a C
extension cannot be forcibly terminated, so a pathological parser could keep
running after its deadline while the job is already recorded as failed. Hard CPU
and memory isolation requires the subprocess worker described in
``docs/PROJECT_ARCHITECTURE.md``; it is not enabled by default.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mafhum_api.db.base import Database, execute_rowcount
from mafhum_api.db.models import (
    DeletionState,
    Document,
    DocumentProcessingJob,
    DocumentStatus,
    DocumentTextSegment,
    JobStatus,
)
from mafhum_api.services.documents.pipeline import (
    PipelineError,
    PipelineLimits,
    PipelineOutcome,
    ProcessingPipeline,
)
from mafhum_api.services.documents.service import DocumentLimits
from mafhum_api.services.documents.storage import DocumentStorage, StorageError
from mafhum_api.services.documents.worker_lease import (
    WorkerAlreadyRunningError,
    WorkerLease,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 0.25


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_count: int
    job_timeout_seconds: int
    stale_after_seconds: int
    max_attempts: int
    poll_interval_seconds: float = _POLL_INTERVAL_SECONDS


class JobClaimError(Exception):
    pass


__all__ = ["JobClaimError", "ProcessingWorker", "WorkerAlreadyRunningError", "WorkerConfig"]


class ProcessingWorker:
    """Owns the thread pool and the claim/execute/persist cycle."""

    def __init__(
        self,
        database: Database,
        storage: DocumentStorage,
        limits: DocumentLimits,
        config: WorkerConfig,
        *,
        pipeline: ProcessingPipeline | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._db = database
        self._storage = storage
        self._limits = limits
        self._config = config
        self._pipeline = pipeline or ProcessingPipeline()
        self._worker_id = worker_id or f"worker-{os.getpid()}"
        self._executor = ThreadPoolExecutor(
            max_workers=config.worker_count, thread_name_prefix="mafhum-doc"
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._futures: set[Future[None]] = set()
        self._lock = threading.Lock()
        self._lease = WorkerLease(database)

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self._dispatcher is not None:
            return
        # Take the single-writer lease before any thread starts, so an unsupported
        # multi-process deployment fails here with an actionable error instead of
        # running quietly in a configuration that was never reasoned about. The
        # lease is taken first so that failing leaves no dispatcher thread behind.
        self._lease.acquire()
        self._stop.clear()
        self._wake.clear()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="mafhum-dispatcher", daemon=True
        )
        self._dispatcher.start()
        logger.info(
            "processing worker started",
            extra={"event": "worker_started", "workers": self._config.worker_count},
        )

    def stop(self, *, wait_seconds: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=wait_seconds)
            self._dispatcher = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        # Released after the dispatcher has stopped, so the lease is never given
        # up while this process could still claim work.
        self._lease.release()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def is_running(self) -> bool:
        """Whether the dispatcher thread is live.

        Exposed so tests and the readiness probe can observe worker state
        without reaching into private attributes.
        """
        return self._dispatcher is not None and self._dispatcher.is_alive()

    def wake(self) -> None:
        """Interrupt the dispatcher's idle wait so a new job starts immediately."""
        self._wake.set()

    def process_pending_now(self, *, max_jobs: int = 64) -> int:
        """Drain the queue synchronously.

        Used by tests and by the development configuration where a background
        thread is undesirable. Returns the number of jobs executed.
        """
        executed = 0
        for _ in range(max_jobs):
            with self._db.unit_of_work() as db:
                self.recover_stale_jobs(db)
            with self._db.unit_of_work() as db:
                job_id = self.claim_next_job(db)
            if job_id is None:
                break
            self.process_job(job_id)
            executed += 1
        return executed

    # -------------------------------------------------------------- dispatcher
    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._db.unit_of_work() as db:
                    self.recover_stale_jobs(db)
                submitted = False
                for _ in range(self._config.worker_count):
                    if self._stop.is_set():
                        break
                    with self._db.unit_of_work() as db:
                        claimed = self.claim_next_job(db)
                    if claimed is None:
                        break
                    submitted = True
                    self._submit(claimed)
                if not submitted:
                    self._wake.wait(self._config.poll_interval_seconds)
                    self._wake.clear()
            except SQLAlchemyError:
                logger.exception("dispatcher database error", extra={"event": "dispatch_error"})
                self._wake.wait(1.0)
                self._wake.clear()
            except Exception:
                logger.exception("dispatcher failure", extra={"event": "dispatch_failure"})
                self._wake.wait(1.0)
                self._wake.clear()

    def _submit(self, job_id: uuid.UUID) -> None:
        future = self._executor.submit(self.process_job, job_id)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._forget)

    def _forget(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)

    # ------------------------------------------------------------------ claim
    def claim_next_job(self, db: Session) -> uuid.UUID | None:
        """Atomically claim the oldest queued job. Returns its id, or ``None``."""
        if self._db.supports_row_locking:
            job = db.scalar(
                select(DocumentProcessingJob)
                .where(DocumentProcessingJob.status == JobStatus.QUEUED.value)
                .order_by(DocumentProcessingJob.queued_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING.value
            job.claimed_at = _utcnow()
            job.heartbeat_at = _utcnow()
            job.attempts = job.attempts + 1
            job.worker_id = self._worker_id
            db.flush()
            return job.id

        # Portable fallback: conditional update acts as the claim.
        candidate = db.scalar(
            select(DocumentProcessingJob.id)
            .where(DocumentProcessingJob.status == JobStatus.QUEUED.value)
            .order_by(DocumentProcessingJob.queued_at)
            .limit(1)
        )
        if candidate is None:
            return None
        now = _utcnow()
        claimed = execute_rowcount(
            db,
            update(DocumentProcessingJob)
            .where(
                DocumentProcessingJob.id == candidate,
                DocumentProcessingJob.status == JobStatus.QUEUED.value,
            )
            .values(
                status=JobStatus.RUNNING.value,
                claimed_at=now,
                heartbeat_at=now,
                attempts=DocumentProcessingJob.attempts + 1,
                worker_id=self._worker_id,
            ),
        )
        if claimed != 1:
            raise JobClaimError("job claimed by another worker")
        return candidate

    # --------------------------------------------------------------- processing
    def process_job(self, job_id: uuid.UUID) -> None:
        """Execute one claimed job to completion (or failure)."""
        try:
            with self._db.unit_of_work() as db:
                job = db.get(DocumentProcessingJob, job_id)
                if job is None:
                    return
                if job.status not in {JobStatus.RUNNING.value, JobStatus.QUEUED.value}:
                    return
                document = db.get(Document, job.document_id)
                if document is None:
                    self._finish_job(db, job, JobStatus.CANCELLED, "missing_document", None)
                    return
                if document.deletion_state != DeletionState.ACTIVE.value:
                    self._finish_job(db, job, JobStatus.CANCELLED, None, None)
                    return
                storage_key = document.storage_key
                document_format = document.format
                generation = job.generation

            path = self._resolve(storage_key)
            if path is None:
                self._record_failure(job_id, "missing_source_file", "ملف المستند غير متوفر.")
                return

            outcome = self._run_with_deadline(path, document_format)
            if outcome is None:
                self._record_failure(job_id, "no_output", "لم تُنتج المعالجة أي نص.")
                return

            with self._db.unit_of_work() as db:
                job = db.get(DocumentProcessingJob, job_id)
                document = db.get(Document, job.document_id) if job else None
                if job is None or document is None:
                    return
                self._persist_outcome(db, document, job, outcome, generation)
        except PipelineError as failure:
            self._record_failure(job_id, failure.code, failure.message)
        except StorageError:
            self._record_failure(job_id, "storage_unavailable", "تعذّر الوصول إلى الملف.")
        except SQLAlchemyError:
            logger.exception("job persistence failed", extra={"event": "job_persist_error"})
            self._record_failure(job_id, "database_error", "تعذّر حفظ نتائج المعالجة.")
        except Exception:
            logger.exception("unexpected job failure", extra={"event": "job_unexpected_error"})
            self._record_failure(job_id, "internal_error", "حدث خطأ غير متوقع أثناء المعالجة.")

    def _run_with_deadline(self, path: Path, document_format: str) -> PipelineOutcome | None:
        """Run the pipeline under a wall-clock deadline."""
        limits = PipelineLimits(
            max_pages=self._limits.max_pdf_pages,
            max_chars=self._limits.max_extracted_chars,
            max_segment_chars=self._limits.max_segment_chars,
        )
        box: list[PipelineOutcome | None] = []
        error: list[BaseException] = []

        def target() -> None:
            try:
                box.append(self._pipeline.run(path, document_format, limits))
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=target, name="mafhum-parse", daemon=True)
        started = time.monotonic()
        thread.start()
        thread.join(self._config.job_timeout_seconds)
        if thread.is_alive():
            elapsed = time.monotonic() - started
            logger.warning(
                "processing exceeded deadline",
                extra={"event": "job_timeout", "seconds": round(elapsed, 2)},
            )
            raise PipelineError("processing_timeout")
        if error:
            raise error[0]
        return box[0] if box else None

    def _persist_outcome(
        self,
        db: Session,
        document: Document,
        job: DocumentProcessingJob,
        outcome: PipelineOutcome,
        generation: int,
    ) -> None:
        """Write segments and flip the document to ``ready`` atomically.

        Old-generation segments are deleted first, inside this transaction, so a
        retry can never leave duplicated text. ``ready`` is only set once the
        segment writes have flushed successfully.
        """
        # Remove every previous segment for this document in the same transaction
        # as the insert: a retry can never leave duplicated or mixed-generation
        # text behind, and UNIQUE(document_id, ordinal) backstops the invariant.
        db.execute(
            delete(DocumentTextSegment).where(DocumentTextSegment.document_id == document.id)
        )
        db.add_all(
            [
                DocumentTextSegment(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    generation=generation,
                    ordinal=segment.ordinal,
                    page_number=segment.page_number,
                    text=segment.text,
                    char_count=segment.char_count,
                )
                for segment in outcome.segments
            ]
        )
        document.page_count = outcome.page_count or None
        document.char_count = outcome.char_count
        document.segment_count = len(outcome.segments)
        document.is_scanned = outcome.is_scanned
        document.parser_name = outcome.parser_name
        document.parser_version = outcome.parser_version
        if outcome.language_hint:
            document.language_hint = outcome.language_hint
        document.status = DocumentStatus.READY.value
        document.processed_at = _utcnow()
        document.error_code = None
        document.error_message = None
        self._finish_job(db, job, JobStatus.SUCCEEDED, None, None)
        db.flush()
        logger.info(
            "document processed",
            extra={
                "event": "document_processed",
                "document_id": str(document.id),
                "segments": len(outcome.segments),
            },
        )

    # ------------------------------------------------------------- failure path
    def _fail(
        self,
        db: Session,
        job: DocumentProcessingJob,
        document: Document,
        code: str,
        message: str | None = None,
    ) -> None:
        exhausted = job.attempts >= self._config.max_attempts
        if exhausted:
            document.status = DocumentStatus.FAILED.value
            document.error_code = code
            document.error_message = message
            self._finish_job(db, job, JobStatus.FAILED, code, message)
        else:
            document.status = DocumentStatus.PENDING.value
            job.status = JobStatus.QUEUED.value
            job.claimed_at = None
            job.worker_id = None
        db.flush()

    def _finish_job(
        self,
        db: Session,
        job: DocumentProcessingJob,
        status: JobStatus,
        code: str | None,
        message: str | None,
    ) -> None:
        job.status = status.value
        job.finished_at = _utcnow()
        job.heartbeat_at = _utcnow()
        job.error_code = code
        job.error_message = message
        db.flush()

    def _record_failure(self, job_id: uuid.UUID, code: str, message: str) -> None:
        logger.info(
            "document processing failed",
            extra={"event": "document_failed", "job_id": str(job_id), "code": code},
        )
        try:
            with self._db.unit_of_work() as db:
                job = db.get(DocumentProcessingJob, job_id)
                if job is None:
                    return
                document = db.get(Document, job.document_id)
                if document is None:
                    return
                self._fail(db, job, document, code, message)
        except SQLAlchemyError:
            logger.exception("could not record failure", extra={"event": "record_failure_error"})

    # ------------------------------------------------------------- maintenance
    def recover_stale_jobs(self, db: Session) -> int:
        """Requeue jobs whose heartbeat has stalled, and fix orphaned documents."""
        cutoff = _utcnow() - timedelta(seconds=self._config.stale_after_seconds)
        stale = db.scalars(
            select(DocumentProcessingJob).where(
                DocumentProcessingJob.status == JobStatus.RUNNING.value,
                and_(
                    (DocumentProcessingJob.heartbeat_at.is_(None))
                    | (DocumentProcessingJob.heartbeat_at < cutoff)
                ),
            )
        ).all()
        recovered = 0
        for job in stale:
            document = db.get(Document, job.document_id)
            if document is None or document.deletion_state != DeletionState.ACTIVE.value:
                job.status = JobStatus.CANCELLED.value
                job.finished_at = _utcnow()
                recovered += 1
                continue
            if job.attempts >= self._config.max_attempts:
                job.status = JobStatus.FAILED.value
                job.finished_at = _utcnow()
                job.error_code = "stale_job"
                job.error_message = "توقفت المعالجة ولم تكتمل."
                document.status = DocumentStatus.FAILED.value
                document.error_code = "stale_job"
                document.error_message = "توقفت المعالجة ولم تكتمل."
                recovered += 1
                continue
            job.status = JobStatus.QUEUED.value
            job.claimed_at = None
            job.worker_id = None
            document.status = DocumentStatus.PENDING.value
            recovered += 1

        # Documents stuck in ``processing`` with no live job at all.
        orphaned = db.scalars(
            select(Document).where(
                Document.status == DocumentStatus.PROCESSING.value,
                Document.deletion_state == DeletionState.ACTIVE.value,
            )
        ).all()
        for document in orphaned:
            has_live_job = db.scalar(
                select(DocumentProcessingJob.id).where(
                    DocumentProcessingJob.document_id == document.id,
                    DocumentProcessingJob.status.in_(
                        [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
                    ),
                )
            )
            if has_live_job is None:
                document.status = DocumentStatus.PENDING.value
                db.add(
                    DocumentProcessingJob(
                        id=uuid.uuid4(),
                        document_id=document.id,
                        generation=_next_generation(db, document.id),
                        status=JobStatus.QUEUED.value,
                    )
                )
                recovered += 1

        if recovered:
            db.flush()
            logger.info(
                "recovered stale processing state",
                extra={"event": "stale_recovery", "count": recovered},
            )
        return recovered

    # ------------------------------------------------------------------ helpers
    def _resolve(self, storage_key: str) -> Path | None:
        resolve = getattr(self._storage, "resolve", None)
        if resolve is None:
            return None
        try:
            path = Path(resolve(storage_key))
        except StorageError:
            return None
        return path if path.is_file() else None


def _next_generation(db: Session, document_id: uuid.UUID) -> int:
    from sqlalchemy import func

    current = db.scalar(
        select(func.coalesce(func.max(DocumentProcessingJob.generation), 0)).where(
            DocumentProcessingJob.document_id == document_id
        )
    )
    return int(current or 0) + 1
