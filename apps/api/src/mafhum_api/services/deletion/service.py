"""Account deletion and document data lifecycle.

Deletion runs in **two phases separated by a commit**, because erasing bytes and
recording that they were erased cannot safely happen in the same transaction.

1. **Tombstone (transactional, touches no bytes).** Sessions are revoked, the
   user flips to ``pending_deletion``, every document is marked
   ``pending_deletion`` with a timestamp, and blobless dependents are dropped.
   Committing this makes the *intent* durable. A rollback here destroys nothing,
   because nothing has been erased yet.
2. **Reclaim (after the commit).** Blobs are deleted, and each document's rows
   are dropped only once its bytes are gone. Whatever cannot be finished stays
   tombstoned in the database, so ``resume_pending_deletions`` — which a periodic
   cleanup job calls — finds and finishes it in a later process.

The commit between the phases is what makes this crash-safe. An in-memory
callback cannot survive the process that registered it; a committed tombstone
can, and it is what lets a fresh process discover outstanding work from the
database alone.

Row deletion is conditional and rowcount-checked. Two concurrent deletions of
one account both pass the ownership read, and an unguarded ORM delete that
matches zero rows makes SQLAlchemy warn — which ``filterwarnings=error`` turns
into a failure.

Incomplete runs leave the user in ``pending_deletion`` and are resumable.
Nothing here claims that backups were purged; backups are out of scope and are
documented as a residual-data limitation in ``docs/DATA_POLICY.md``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mafhum_api.db.base import Base, execute_rowcount
from mafhum_api.db.models import (
    DeletionState,
    Document,
    DocumentProcessingJob,
    DocumentTextSegment,
    PresentationArtifact,
    PresentationJob,
    Project,
    SourceRecord,
    User,
    UserSession,
)
from mafhum_api.services.documents.storage import DocumentStorage, StorageError

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DeletionReport:
    user_id: uuid.UUID
    documents_removed: int = 0
    documents_tombstoned: int = 0
    blobs_removed: int = 0
    blobs_failed: list[str] = field(default_factory=list)
    segments_removed: int = 0
    jobs_removed: int = 0
    sources_removed: int = 0
    projects_removed: int = 0
    sessions_revoked: int = 0
    complete: bool = False
    error: str | None = None

    @property
    def has_failures(self) -> bool:
        return bool(self.blobs_failed) or self.error is not None


class DeletionService:
    def __init__(self, storage: DocumentStorage) -> None:
        self._storage = storage

    def delete_account(self, db: Session, user_id: uuid.UUID) -> DeletionReport:
        """Run the full deletion lifecycle for one account.

        Owns its transaction boundaries: the tombstone is committed before any
        byte is touched, so a failure during reclamation can never resurrect a
        row whose bytes are gone.
        """
        report = self.tombstone_account(db, user_id)
        if report.error is not None:
            return report

        try:
            # Durable intent. Past this point a crash leaves recoverable work in
            # the database rather than an inconsistent filesystem.
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            report.error = "database_error"
            return report

        return self.reclaim_account(db, user_id, report)

    def tombstone_account(self, db: Session, user_id: uuid.UUID) -> DeletionReport:
        """Phase 1: block access and record what must be erased. Touches no bytes.

        The caller commits. Safe to run repeatedly — an already-tombstoned
        account is simply re-tombstoned.
        """
        report = DeletionReport(user_id=user_id)

        # The tombstone is a conditional Core UPDATE, not an ORM attribute
        # assignment. Two concurrent deletions both read the row; whichever runs
        # second would otherwise flush ``UPDATE users ... WHERE id = ?`` against a
        # row the winner has already removed, and SQLAlchemy raises
        # StaleDataError -- a 500 for a request that should simply report that the
        # account is gone.
        tombstoned = execute_rowcount(
            db,
            update(User)
            .where(User.id == user_id)
            .values(deletion_state=DeletionState.PENDING_DELETION.value),
        )
        if tombstoned == 0:
            # Read the id as a column: `db.get` would return a stale identity-map
            # instance and report the account as present after it was deleted.
            still_there = db.scalar(select(User.id).where(User.id == user_id))
            if still_there is None:
                report.error = "user_not_found"
                return report

        report.sessions_revoked = self._revoke_sessions(db, user_id)
        report.documents_tombstoned = execute_rowcount(
            db,
            update(Document)
            .where(
                Document.owner_id == user_id,
                Document.deletion_state != DeletionState.PENDING_DELETION.value,
            )
            .values(
                deletion_state=DeletionState.PENDING_DELETION.value,
                deleted_at=_utcnow(),
            ),
        )
        # Blobless dependents can go now; they have nothing to reclaim.
        report.sources_removed += _delete_where(db, SourceRecord, SourceRecord.owner_id == user_id)
        report.projects_removed += _delete_where(db, Project, Project.owner_id == user_id)
        db.flush()
        return report

    def reclaim_account(
        self, db: Session, user_id: uuid.UUID, report: DeletionReport | None = None
    ) -> DeletionReport:
        """Phase 2: erase the bytes, then the rows that pointed at them.

        Idempotent and resumable. A blob that cannot be deleted leaves its
        document tombstoned for the next run rather than aborting the account.
        """
        report = report if report is not None else DeletionReport(user_id=user_id)

        documents = list(
            db.scalars(
                select(Document)
                .where(
                    Document.owner_id == user_id,
                    Document.deletion_state == DeletionState.PENDING_DELETION.value,
                )
                .with_for_update(skip_locked=True)
            ).all()
        )
        for document in documents:
            key = document.storage_key
            try:
                if self._storage.delete(key):
                    report.blobs_removed += 1
            except StorageError:
                logger.error(
                    "account deletion: blob removal failed",
                    extra={"event": "deletion_blob_failed", "user_id": str(user_id)},
                )
                report.blobs_failed.append(key)
                continue

            # Bytes are gone, so the rows are safe to drop.
            report.segments_removed += _delete_where(
                db, DocumentTextSegment, DocumentTextSegment.document_id == document.id
            )
            report.jobs_removed += _delete_where(
                db, DocumentProcessingJob, DocumentProcessingJob.document_id == document.id
            )
            report.sources_removed += _delete_where(
                db, SourceRecord, SourceRecord.document_id == document.id
            )
            if self._delete_document_row(db, document.id, user_id):
                report.documents_removed += 1
        db.flush()

        # Phase 6: generated PPTX files are user data too. A DB cascade would
        # drop the rows but leave the bytes behind, so erase each blob first and
        # keep the account tombstoned if any deletion fails.
        artifacts = list(
            db.scalars(
                select(PresentationArtifact).where(PresentationArtifact.owner_id == user_id)
            ).all()
        )
        for artifact in artifacts:
            key = artifact.storage_key
            try:
                if self._storage.delete(key):
                    report.blobs_removed += 1
            except StorageError:
                logger.error(
                    "account deletion: presentation blob removal failed",
                    extra={"event": "deletion_artifact_blob_failed"},
                )
                report.blobs_failed.append(key)
                continue
            db.delete(artifact)
        if artifacts:
            db.flush()
        _delete_where(db, PresentationJob, PresentationJob.owner_id == user_id)

        if report.blobs_failed:
            # Stay tombstoned and inaccessible; a later run finishes the job.
            report.complete = False
            logger.warning(
                "account deletion incomplete; blobs retained for retry",
                extra={
                    "event": "deletion_incomplete",
                    "user_id": str(user_id),
                    "pending_blobs": len(report.blobs_failed),
                },
            )
            return report

        outcome = self._remove_account_row(db, user_id)
        if outcome == "database_error":
            report.error = "database_error"
            return report
        if outcome == "documents_remain":
            # Another caller holds the remaining rows (reclaim skips locked rows
            # rather than blocking on them). Not an error, but not finished.
            report.complete = False
            return report

        report.complete = True
        logger.info(
            "account deleted",
            extra={
                "event": "account_deleted",
                "documents": report.documents_removed,
                "blobs": report.blobs_removed,
            },
        )
        return report

    def resume_pending_deletions(self, db: Session) -> list[DeletionReport]:
        """Finish deletions whose reclamation did not complete.

        Reads the outstanding work from the committed tombstones, so it also
        recovers from a process that died mid-cleanup.
        """
        user_ids = list(
            db.scalars(
                select(User.id).where(User.deletion_state == DeletionState.PENDING_DELETION.value)
            ).all()
        )
        reports: list[DeletionReport] = []
        for user_id in user_ids:
            report = self.reclaim_account(db, user_id)
            db.commit()
            reports.append(report)
        return reports

    def purge_pending_documents(self, db: Session) -> tuple[int, list[str]]:
        """Reclaim blobs for documents tombstoned ``pending_deletion``.

        Returns ``(removed, failed)``. Safe to run repeatedly.
        """
        documents = list(
            db.scalars(
                select(Document).where(
                    Document.deletion_state == DeletionState.PENDING_DELETION.value
                )
            ).all()
        )
        removed = 0
        failed: list[str] = []
        for document in documents:
            try:
                if self._storage.delete(document.storage_key):
                    removed += 1
            except StorageError:
                failed.append(document.storage_key)
                continue
            _delete_where(db, DocumentTextSegment, DocumentTextSegment.document_id == document.id)
            _delete_where(
                db, DocumentProcessingJob, DocumentProcessingJob.document_id == document.id
            )
            self._delete_document_row(db, document.id, document.owner_id)
        db.flush()
        if removed:
            logger.info(
                "purged tombstoned documents",
                extra={"event": "pending_documents_purged", "count": removed},
            )
        return removed, failed

    # ------------------------------------------------------------------ helpers
    def _revoke_sessions(self, db: Session, user_id: uuid.UUID) -> int:
        return execute_rowcount(
            db,
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=_utcnow()),
        )

    def _delete_document_row(
        self, db: Session, document_id: uuid.UUID, owner_id: uuid.UUID
    ) -> bool:
        """Drop one document row, reporting whether this caller actually did it.

        Conditional on ownership and checked by rowcount: of two concurrent
        deletions only one matches, and the loser reports no work instead of
        claiming a delete it did not perform.
        """
        return (
            execute_rowcount(
                db,
                delete(Document).where(
                    Document.id == document_id,
                    Document.owner_id == owner_id,
                ),
            )
            > 0
        )

    def _remove_account_row(self, db: Session, user_id: uuid.UUID) -> str:
        """Drop the account row once nothing of its own remains.

        Returns ``"deleted"``, ``"documents_remain"`` or ``"database_error"``.
        Another caller having already removed the row counts as ``"deleted"``:
        the intended end state was reached.
        """
        remaining = (
            db.scalar(
                select(func.count()).select_from(Document).where(Document.owner_id == user_id)
            )
            or 0
        )
        remaining_artifacts = (
            db.scalar(
                select(func.count())
                .select_from(PresentationArtifact)
                .where(PresentationArtifact.owner_id == user_id)
            )
            or 0
        )
        if remaining or remaining_artifacts:
            # Documents still exist, so their bytes are still owed.
            return "documents_remain"
        try:
            execute_rowcount(db, delete(User).where(User.id == user_id))
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            return "database_error"
        return "deleted"


def _delete_where(db: Session, model: type[Base], *conditions: object) -> int:
    """Bulk-delete and report the affected row count."""
    return execute_rowcount(db, delete(model).where(*conditions))  # type: ignore[arg-type]
