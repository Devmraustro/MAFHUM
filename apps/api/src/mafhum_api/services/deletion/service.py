"""Account deletion and document data lifecycle.

Ordering matters for safety:

1. **Block access first.** The user is flipped to ``pending_deletion`` and every
   session is revoked before any file is touched, so no request can read the
   account's data while deletion is in flight.
2. **Delete blobs, tolerating failure.** Each stored file is removed; failures are
   collected rather than aborting the run.
3. **Delete rows only once their files are gone.** A row is never removed while
   its blob still exists, because a row is the only record that the blob belongs
   to someone and must be reclaimed.
4. **Report honestly.** The result states what was deleted and what was not.
   Nothing here claims that backups were purged — backups are out of scope and
   are documented as a residual-data limitation in ``docs/DATA_POLICY.md``.

Incomplete runs leave the user in ``pending_deletion`` and are resumable by
``resume_pending_deletions``, which is also what a periodic cleanup job calls.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mafhum_api.db.base import Base, execute_rowcount
from mafhum_api.db.models import (
    DeletionState,
    Document,
    DocumentProcessingJob,
    DocumentTextSegment,
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
        """Run the full deletion lifecycle for one account."""
        report = DeletionReport(user_id=user_id)
        user = db.get(User, user_id)
        if user is None:
            report.error = "user_not_found"
            return report

        # Step 1: block access immediately.
        report.sessions_revoked = self._revoke_sessions(db, user_id)
        if user.deletion_state == DeletionState.ACTIVE.value:
            user.deletion_state = DeletionState.PENDING_DELETION.value
        db.flush()

        # Step 2 + 3: documents, their blobs and their dependents.
        documents = list(db.scalars(select(Document).where(Document.owner_id == user_id)).all())
        remaining_keys: list[str] = []
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
                remaining_keys.append(key)
                continue

            # Blob is gone: now the rows are safe to drop.
            report.segments_removed += _delete_where(
                db, DocumentTextSegment, DocumentTextSegment.document_id == document.id
            )
            report.jobs_removed += _delete_where(
                db, DocumentProcessingJob, DocumentProcessingJob.document_id == document.id
            )
            report.sources_removed += _delete_where(
                db, SourceRecord, SourceRecord.document_id == document.id
            )
            db.delete(document)
            report.documents_removed += 1
        db.flush()

        # Step 4: the rest of the account.
        report.sources_removed += _delete_where(db, SourceRecord, SourceRecord.owner_id == user_id)
        report.projects_removed += _delete_where(db, Project, Project.owner_id == user_id)

        if report.blobs_failed:
            # Keep the account tombstoned so it stays inaccessible, and leave the
            # rows that still own blobs so reconciliation can finish the job.
            self._mark_pending_documents(db, remaining_keys)
            user.deletion_state = DeletionState.PENDING_DELETION.value
            report.complete = False
            db.flush()
            logger.warning(
                "account deletion incomplete; blobs retained for retry",
                extra={
                    "event": "deletion_incomplete",
                    "user_id": str(user_id),
                    "pending_blobs": len(report.blobs_failed),
                },
            )
            return report

        db.delete(user)
        try:
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            report.error = "database_error"
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
        """Finish deletions whose blob removal previously failed."""
        users = list(
            db.scalars(
                select(User).where(User.deletion_state == DeletionState.PENDING_DELETION.value)
            ).all()
        )
        return [self.delete_account(db, user.id) for user in users]

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
            db.delete(document)
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

    def _mark_pending_documents(self, db: Session, keys: list[str]) -> None:
        if not keys:
            return
        db.execute(
            update(Document)
            .where(Document.storage_key.in_(keys))
            .values(deletion_state=DeletionState.PENDING_DELETION.value, deleted_at=_utcnow())
        )


def _delete_where(db: Session, model: type[Base], *conditions: object) -> int:
    """Bulk-delete and report the affected row count."""
    return execute_rowcount(db, delete(model).where(*conditions))  # type: ignore[arg-type]
