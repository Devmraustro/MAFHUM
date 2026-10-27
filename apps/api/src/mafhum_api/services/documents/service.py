"""Document service: the only component that mutates document state.

Concurrency and consistency rules
---------------------------------
* **Quota enforcement** locks the owning user row (``SELECT ... FOR UPDATE``)
  before counting, so simultaneous uploads from one user cannot both pass a
  boundary check. This is a database-level guarantee on PostgreSQL.
* **Write ordering** is a saga: the blob is written first (so its true size is
  known), then the row is inserted. Any failure after the blob write triggers
  compensation (blob deletion), and ``reconcile_orphan_blobs`` catches crashes
  between the two steps.
* **Retry idempotency** uses a monotonically increasing ``generation``. New
  segments are written and old-generation segments deleted in the *same*
  transaction, and ``UNIQUE(document_id, ordinal)`` makes a double-insert
  impossible rather than merely unlikely.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import and_, delete, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mafhum_api.core.errors import (
    AuthorizationError,
    ConflictError,
    FileRejectedError,
    NotFoundError,
    ProcessingUnavailableError,
    QuotaExceededError,
    ValidationError,
)
from mafhum_api.db.base import execute_rowcount
from mafhum_api.db.models import (
    DOCUMENT_TRANSITIONS,
    DeletionState,
    Document,
    DocumentFormat,
    DocumentProcessingJob,
    DocumentStatus,
    DocumentTextSegment,
    JobStatus,
    Project,
    User,
)
from mafhum_api.services.documents import validators
from mafhum_api.services.documents.scanning import (
    MalwareScanner,
    NullScanner,
    ScanVerdict,
    requires_clean,
)
from mafhum_api.services.documents.storage import (
    DocumentStorage,
    StorageError,
    StorageObjectNotFoundError,
)

logger = logging.getLogger(__name__)

_READ_CHUNK = 1024 * 256
_SIGNATURE_BYTES = 16
# How many leading bytes we inspect for magic-number detection.
MAX_PAGE_SIZE_DEFAULT = 20
SHA_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    max_upload_bytes: int
    max_extracted_chars: int
    max_pdf_pages: int
    max_docx_entries: int
    max_docx_uncompressed_bytes: int
    max_archive_compression_ratio: float
    max_documents_per_user: int
    max_total_bytes_per_user: int
    max_segment_chars: int


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    id: uuid.UUID
    display_filename: str
    format: str
    size_bytes: int
    status: str
    page_count: int | None
    char_count: int
    segment_count: int
    project_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    is_scanned: bool | None
    language_hint: str | None
    created_at: datetime
    processed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TextPage:
    segments: Sequence[tuple[int, uuid.UUID, int | None, str]]
    total: int
    page: int
    page_size: int
    document_status: str


class DocumentService:
    def __init__(
        self,
        storage: DocumentStorage,
        limits: DocumentLimits,
        *,
        row_locking: bool = True,
        scanner: MalwareScanner | None = None,
        scan_required: bool = False,
    ) -> None:
        self._storage = storage
        self._limits = limits
        self._row_locking = row_locking
        self._scanner = scanner or NullScanner()
        self._scan_required = scan_required

    @property
    def scanner(self) -> MalwareScanner:
        """The scanner this service actually applies to uploads.

        Exposed so the API's capability disclosure is read from the same object
        that does the work. A separate module-level scanner would let the
        advertised state drift from the real one.
        """
        return self._scanner

    @property
    def scan_required(self) -> bool:
        """Whether an upload is rejected unless an engine vouches for it."""
        return self._scan_required

    # ------------------------------------------------------------------ upload
    def upload(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        fileobj: BinaryReader,
        project_id: uuid.UUID | None = None,
        language_hint: str | None = None,
    ) -> Document:
        """Validate, store and register an upload, then queue processing.

        ``fileobj`` must expose ``read(size) -> bytes``; it is consumed in
        bounded chunks so memory use does not grow with the upload size.
        """
        display_name = validators.sanitize_display_name(filename)
        declared_format = validators.check_extension_and_mime(filename, content_type)

        if project_id is not None:
            self._assert_project_owned(db, owner_id, project_id)

        # Cheap, unlocked pre-check for fast feedback; re-checked under lock below.
        self._soft_quota_check(db, owner_id)

        storage_key = self._storage.generate_key()
        reader = _ChunkReader(fileobj, _READ_CHUNK)
        head = reader.peek(_SIGNATURE_BYTES)
        detected = validators.check_signature(head, declared_format)

        try:
            stored = self._storage.write(
                storage_key, reader, max_bytes=self._limits.max_upload_bytes
            )
        except StorageError as exc:
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_TOO_LARGE],
                details={"reason": validators.REASON_TOO_LARGE},
            ) from exc

        # From here on a blob exists on disk. ANY failure before the row is
        # committed must remove it, otherwise rejected uploads accumulate as
        # orphans that no database row accounts for.
        try:
            digest, size_bytes = self._digest(storage_key)
            if size_bytes != stored.size_bytes:
                raise FileRejectedError(
                    validators.ARABIC_MESSAGES[validators.REASON_MALFORMED],
                    details={"reason": validators.REASON_MALFORMED},
                )
            validators.check_size(size_bytes, self._validation_limits())

            # Format-specific structural validation, on the stored bytes.
            if detected == DocumentFormat.DOCX.value:
                self._validate_docx_container(storage_key)
            elif detected == DocumentFormat.PDF.value:
                self._validate_pdf_page_count(storage_key)

            # Content scanning, on the stored bytes. Structural validation above
            # is NOT scanning: it rejects malformed and abusive files but detects
            # no malicious content, so it cannot stand in for this step.
            verdict = self._scan_bytes(storage_key, display_name)

            document = Document(
                id=uuid.uuid4(),
                owner_id=owner_id,
                project_id=project_id,
                storage_key=storage_key,
                display_filename=display_name,
                format=detected,
                size_bytes=size_bytes,
                content_sha256=digest,
                status=DocumentStatus.PENDING.value,
                language_hint=(language_hint or None),
                # NULL would mean "never asked"; recording the real verdict is
                # what lets an audit tell a scanned file from an unscanned one.
                scan_verdict=verdict.value,
            )
            try:
                self._enforce_quota(db, owner_id, size_bytes)
                db.add(document)
                db.add(
                    DocumentProcessingJob(
                        id=uuid.uuid4(),
                        document_id=document.id,
                        generation=1,
                        status=JobStatus.QUEUED.value,
                    )
                )
                db.flush()
            except SQLAlchemyError:
                db.rollback()
                raise
        except BaseException:
            # Covers validation failures, quota rejections, database errors and
            # interrupts alike: the blob never outlives a failed upload.
            self._compensate(storage_key)
            raise

        logger.info(
            "document uploaded",
            extra={"event": "document_uploaded", "document_id": str(document.id)},
        )
        return document

    # -------------------------------------------------------------------- read
    def get_owned(self, db: Session, owner_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        """Fetch a document, enforcing ownership in the query itself."""
        document = db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.deletion_state != DeletionState.DELETED.value,
            )
        )
        if document is None:
            # Same error for missing and not-owned: no existence oracle.
            raise AuthorizationError()
        return document

    def list_owned(
        self,
        db: Session,
        owner_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE_DEFAULT,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[Document], int]:
        conditions = [
            Document.owner_id == owner_id,
            Document.deletion_state != DeletionState.DELETED.value,
        ]
        if project_id is not None:
            conditions.append(Document.project_id == project_id)
        if status is not None:
            conditions.append(Document.status == status)
        total = db.scalar(select(func.count()).select_from(Document).where(and_(*conditions)))
        rows = db.scalars(
            select(Document)
            .where(and_(*conditions))
            .order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), int(total or 0)

    def get_text(
        self,
        db: Session,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = MAX_PAGE_SIZE_DEFAULT,
    ) -> TextPage:
        document = self.get_owned(db, owner_id, document_id)
        conditions = [DocumentTextSegment.document_id == document.id]
        total = int(
            db.scalar(
                select(func.count()).select_from(DocumentTextSegment).where(and_(*conditions))
            )
            or 0
        )
        rows = db.execute(
            select(
                DocumentTextSegment.ordinal,
                DocumentTextSegment.id,
                DocumentTextSegment.page_number,
                DocumentTextSegment.text,
            )
            .where(and_(*conditions))
            .order_by(DocumentTextSegment.ordinal)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        segments = [(int(r[0]), r[1], r[2], r[3]) for r in rows]
        return TextPage(
            segments=segments,
            total=total,
            page=page,
            page_size=page_size,
            document_status=document.status,
        )

    def open_stream(
        self, db: Session, owner_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[Iterator[bytes], Document]:
        """Return a byte stream only after ownership has been verified."""
        document = self.get_owned(db, owner_id, document_id)
        try:
            stream = self._storage.open_read(document.storage_key)
        except StorageObjectNotFoundError as exc:
            raise NotFoundError("محتوى المستند غير متوفر.") from exc
        except StorageError as exc:
            raise NotFoundError("محتوى المستند غير متوفر.") from exc
        return stream, document

    # ------------------------------------------------------------------ retry
    def retry(
        self, db: Session, owner_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentProcessingJob:
        """Queue a new processing generation for a failed document.

        Idempotent **and** race-safe. The document row is taken ``FOR UPDATE``
        before any decision is made, so concurrent retries serialise on that row
        lock and every caller after the first observes the winner's committed
        job instead of inventing a second run.

        Two defects lived here before:

        * the generation was computed as ``MAX(generation) + 1`` after a plain
          SELECT, so two concurrent callers picked the same generation and
          collided on the ``(document_id, generation)`` unique constraint — a
          500 for the client;
        * a ``PENDING`` document returned the existing job only when it was
          ``PROCESSING``, so a second retry queued a duplicate run.
        """
        # ``get_owned`` supplies the 404 semantics; the locking re-read below is
        # what actually serialises concurrent callers.
        #
        # ``populate_existing`` is load-bearing, not cosmetic: ``get_owned`` has
        # already put this row in the identity map, and a plain query returns the
        # cached instance *without* refreshing its attributes. Without it the
        # caller keeps reading the pre-lock status ('failed') even though the row
        # it now holds says 'pending', and every concurrent retry queues its own
        # duplicate job.
        document = self.get_owned(db, owner_id, document_id)
        locked = db.execute(
            select(Document)
            .where(Document.id == document.id, Document.owner_id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if locked is None:
            # Deleted between the ownership check and the lock (a concurrent
            # delete won the race). Same answer the ownership check gives,
            # just observed later -- not an escaped 500.
            raise AuthorizationError()
        document = locked

        if document.status == DocumentStatus.READY.value:
            raise ConflictError("المستند تمت معالجته بالفعل.")
        if document.status in {
            DocumentStatus.PROCESSING.value,
            DocumentStatus.PENDING.value,
        }:
            existing = self._in_flight_job(db, document.id)
            if existing is not None:
                return existing
        if document.status not in {
            DocumentStatus.FAILED.value,
            DocumentStatus.PENDING.value,
        }:
            raise ConflictError("لا يمكن إعادة المحاولة في الحالة الحالية.")

        next_generation = self._next_generation(db, document.id)
        self._transition(db, document, DocumentStatus(document.status), DocumentStatus.PENDING)
        document.error_code = None
        document.error_message = None
        job = DocumentProcessingJob(
            id=uuid.uuid4(),
            document_id=document.id,
            generation=next_generation,
            status=JobStatus.QUEUED.value,
        )
        db.add(job)
        db.flush()
        logger.info(
            "document retry queued",
            extra={"event": "document_retry", "document_id": str(document.id)},
        )
        return job

    def _in_flight_job(self, db: Session, document_id: uuid.UUID) -> DocumentProcessingJob | None:
        """Return the newest queued or running job for a document, if any."""
        return db.scalar(
            select(DocumentProcessingJob)
            .where(
                DocumentProcessingJob.document_id == document_id,
                DocumentProcessingJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .order_by(DocumentProcessingJob.generation.desc())
        )

    # ----------------------------------------------------------------- delete
    def delete(self, db: Session, owner_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Delete a document, its segments, its jobs and its stored blob.

        The blob is removed only after the transaction commits, so a rollback can
        never leave a row pointing at deleted bytes. A filesystem failure does not
        resurrect the row: the document is marked ``pending_deletion`` and handed
        to reconciliation.
        """
        document = self.get_owned(db, owner_id, document_id)
        storage_key = document.storage_key

        # Lock the parent row before touching children. A concurrent retry
        # serialises on this same lock; without it, retry could pass its own
        # lock, queue a job and commit after the child deletes below had run,
        # leaving a job whose document no longer exists. A document that
        # vanishes before the lock is the usual 404, not a 500.
        if self._row_locking:
            locked = db.execute(
                select(Document)
                .where(Document.id == document.id, Document.owner_id == owner_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if locked is None:
                raise AuthorizationError()

        db.execute(
            delete(DocumentTextSegment).where(DocumentTextSegment.document_id == document.id)
        )
        db.execute(
            delete(DocumentProcessingJob).where(DocumentProcessingJob.document_id == document.id)
        )
        # Gate on the rowcount of the parent delete. Two concurrent deletes both
        # pass the ownership SELECT; only one matches a row. Without this check
        # the loser still returned ``deleted: true`` for a delete it did not
        # perform, and SQLAlchemy warned that 0 rows were matched. The rollback
        # undoes the child deletes in this transaction, so nothing is lost.
        removed = execute_rowcount(
            db,
            delete(Document).where(
                Document.id == document.id,
                Document.owner_id == owner_id,
            ),
        )
        if removed == 0:
            db.rollback()
            raise AuthorizationError()
        try:
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            raise

        # Register, do not execute: the bytes may only go once the transaction
        # is durable. The endpoint commits *after* this method returns, so
        # deleting here would leave a surviving row pointing at missing bytes
        # whenever the commit failed — a document that still lists, opens and
        # authorises, but can never be downloaded.
        self._delete_blob_after_commit(db, storage_key)

    def _delete_blob_after_commit(self, db: Session, storage_key: str) -> None:
        """Remove a blob only if the enclosing transaction commits.

        A rollback detaches the listener without touching the filesystem, so the
        bytes outlive any transaction that did not become durable. Listeners are
        one-shot and removed on either outcome so a pooled session cannot carry
        a stale deletion into a later request.
        """

        # ``event.remove`` cannot be called from inside a handler — SQLAlchemy is
        # iterating its listener deque at that point and raises. A one-shot flag
        # gives the same guarantee; the listeners are attached to this Session
        # instance only, so they are discarded with it at the end of the request.
        settled = False

        def after_commit(session: Session) -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            try:
                self._storage.delete(storage_key)
            except StorageError:
                # The row is already gone, so nothing can point at these bytes;
                # reconciliation reclaims them later.
                logger.error(
                    "blob deletion failed after document removal; queued for reconciliation",
                    extra={"event": "document_blob_orphaned"},
                )

        def after_rollback(session: Session) -> None:
            # The row survived, so the bytes must survive with it.
            nonlocal settled
            settled = True

        event.listen(db, "after_commit", after_commit)
        event.listen(db, "after_rollback", after_rollback)

    # ------------------------------------------------------------- maintenance
    def reconcile_orphan_blobs(self, db: Session) -> tuple[list[str], list[str]]:
        """Delete blobs with no live row. Returns ``(deleted, failed)``."""
        live_keys = list(db.scalars(select(Document.storage_key)).all())
        orphans = self._storage.reconcile_orphans(live_keys)
        if not orphans:
            return [], []
        deleted, failed = self._storage.delete_many(orphans)
        if deleted:
            logger.info(
                "reclaimed orphan blobs",
                extra={"event": "orphan_blobs_reclaimed", "count": len(deleted)},
            )
        return deleted, failed

    def storage_usage(self, db: Session, owner_id: uuid.UUID) -> tuple[int, int]:
        """Return ``(document_count, total_bytes)`` for a user."""
        row = db.execute(
            select(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.owner_id == owner_id,
                Document.deletion_state != DeletionState.DELETED.value,
            )
        ).one()
        return int(row[0]), int(row[1])

    # ------------------------------------------------------------------ helpers
    def summarize(self, document: Document) -> DocumentSummary:
        return DocumentSummary(
            id=document.id,
            display_filename=document.display_filename,
            format=document.format,
            size_bytes=document.size_bytes,
            status=document.status,
            page_count=document.page_count,
            char_count=document.char_count,
            segment_count=document.segment_count,
            project_id=document.project_id,
            error_code=document.error_code,
            error_message=document.error_message,
            is_scanned=document.is_scanned,
            language_hint=document.language_hint,
            created_at=document.created_at,
            processed_at=document.processed_at,
        )

    def _assert_project_owned(
        self, db: Session, owner_id: uuid.UUID, project_id: uuid.UUID
    ) -> Project:
        project = db.scalar(
            select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        )
        if project is None:
            # Identical to a missing project: never disclose another user's id.
            raise ValidationError(
                "المشروع المحدد غير متاح.",
                field_errors={"project_id": ["غير متاح."]},
            )
        return project

    def _soft_quota_check(self, db: Session, owner_id: uuid.UUID) -> None:
        count, total_bytes = self.storage_usage(db, owner_id)
        if count >= self._limits.max_documents_per_user:
            raise QuotaExceededError(
                f"وصلت إلى الحد الأقصى لعدد المستندات ({self._limits.max_documents_per_user})."
            )
        if total_bytes >= self._limits.max_total_bytes_per_user:
            raise QuotaExceededError("وصلت إلى الحد الأقصى لمساحة التخزين.")

    def _enforce_quota(self, db: Session, owner_id: uuid.UUID, additional_bytes: int) -> None:
        """Authoritative quota check, serialized on the owning user row."""
        if self._row_locking:
            user = db.execute(
                select(User).where(User.id == owner_id).with_for_update()
            ).scalar_one_or_none()
            if user is None:
                raise AuthorizationError()
        count, total_bytes = self.storage_usage(db, owner_id)
        if count >= self._limits.max_documents_per_user:
            raise QuotaExceededError(
                f"وصلت إلى الحد الأقصى لعدد المستندات ({self._limits.max_documents_per_user})."
            )
        if total_bytes + additional_bytes > self._limits.max_total_bytes_per_user:
            raise QuotaExceededError("رفع هذا الملف سيتجاوز الحد الأقصى لمساحة التخزين المخصصة لك.")

    def _scan_bytes(self, storage_key: str, filename: str) -> ScanVerdict:
        """Scan the stored blob and apply the configured policy.

        Outcomes are kept strictly distinct, because conflating them is how an
        unscanned file ends up looking safe:

        * ``CLEAN`` -- a real engine examined the bytes and found nothing.
        * ``INFECTED`` -- a real engine matched a signature. Rejected.
        * ``ERROR`` -- an engine was configured but could not answer. Not a pass.
        * ``NOT_SCANNED`` -- no engine is configured. Also not a pass.

        Only ``CLEAN`` counts as vouched for. With ``malware_scan_required`` on,
        the other three reject the upload, so an unreachable daemon blocks uploads
        instead of waving them through.
        """
        verdict = self._scan_stored(storage_key, filename)
        if verdict is ScanVerdict.INFECTED:
            raise FileRejectedError(
                "تم رفض الملف: تم رصد محتوى ضار.",
                details={"reason": "malware_detected"},
            )
        if self._scan_required and not requires_clean(verdict):
            raise FileRejectedError(
                "تعذّر فحص الملف، والفحص مطلوب. حاول مرة أخرى لاحقًا.",
                details={"reason": f"scan_{verdict.value}"},
            )
        return verdict

    def _scan_stored(self, storage_key: str, filename: str) -> ScanVerdict:
        """Stream the stored blob through the scanner without buffering it.

        Storage yields chunks rather than a file handle, so the chunks are wrapped
        in a minimal ``read()`` adapter. That keeps scanning off the "hold the
        whole upload in memory a second time" path -- the blob is already on disk
        and the daemon wants a stream anyway.
        """
        scanner = self._scanner
        try:
            chunks = self._storage.open_read(storage_key)
            scan_stream = getattr(scanner, "scan_stream", None)
            if scan_stream is not None:
                return ScanVerdict(
                    scan_stream(_ChunkStream(chunks), filename=filename, size=0).value
                )
            data = b"".join(chunks)
        except StorageError:
            # The blob could not be read, so nothing examined it.
            return ScanVerdict.ERROR
        return scanner.scan(data, filename)

    def _next_generation(self, db: Session, document_id: uuid.UUID) -> int:
        current = db.scalar(
            select(func.coalesce(func.max(DocumentProcessingJob.generation), 0)).where(
                DocumentProcessingJob.document_id == document_id
            )
        )
        return int(current or 0) + 1

    def _transition(
        self,
        db: Session,
        document: Document,
        current: DocumentStatus,
        target: DocumentStatus,
    ) -> None:
        if target not in DOCUMENT_TRANSITIONS[current]:
            raise ConflictError("لا يمكن تغيير حالة المستند بهذه الطريقة.")
        document.status = target.value

    def _validation_limits(self) -> validators.ValidationLimits:
        return validators.ValidationLimits(
            max_upload_bytes=self._limits.max_upload_bytes,
            max_pdf_pages=self._limits.max_pdf_pages,
            max_docx_entries=self._limits.max_docx_entries,
            max_docx_uncompressed_bytes=self._limits.max_docx_uncompressed_bytes,
            max_archive_compression_ratio=self._limits.max_archive_compression_ratio,
        )

    def _validate_docx_container(self, storage_key: str) -> None:
        path = self._storage_path(storage_key)
        validators.check_archive_shape(path, self._validation_limits())

    def _validate_pdf_page_count(self, storage_key: str) -> None:
        """Reject over-long PDFs before any parsing work happens."""
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        path = self._storage_path(storage_key)
        try:
            reader = PdfReader(str(path), strict=False)
        except (PdfReadError, OSError, ValueError) as exc:
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_MALFORMED],
                details={"reason": validators.REASON_MALFORMED},
            ) from exc
        if getattr(reader, "is_encrypted", False):
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_ENCRYPTED],
                details={"reason": validators.REASON_ENCRYPTED},
            )
        validators.check_page_count(len(reader.pages), self._validation_limits())

    def _storage_path(self, storage_key: str) -> Path:
        resolve = getattr(self._storage, "resolve", None)
        if resolve is None:  # pragma: no cover - defensive for alt backends
            raise ProcessingUnavailableError()
        return Path(resolve(storage_key))

    def _digest(self, storage_key: str) -> tuple[str, int]:
        hasher = hashlib.sha256()
        total = 0
        for chunk in self._storage.open_read(storage_key):
            hasher.update(chunk)
            total += len(chunk)
        return hasher.hexdigest(), total

    def _compensate(self, storage_key: str) -> None:
        try:
            self._storage.delete(storage_key)
        except StorageError:
            logger.error(
                "compensating blob deletion failed",
                extra={"event": "compensation_failed"},
            )


@runtime_checkable
class BinaryReader(Protocol):
    """Minimal synchronous file-like reader accepted by ``upload``."""

    def read(self, size: int = -1, /) -> bytes: ...


class _ChunkStream:
    """Adapt an iterator of byte chunks to the ``read(n)`` a scanner expects.

    Deliberately minimal: no seek, no tell. Scanning reads forward exactly once.
    """

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            parts = [self._buffer, *self._chunks]
            self._buffer = b""
            return b"".join(parts)
        while len(self._buffer) < size:
            try:
                chunk = next(self._chunks)
            except StopIteration:
                break
            if chunk:
                self._buffer += chunk
        out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out


class _ChunkReader(Iterable[bytes]):
    """Bounded reader over a file-like object.

    Reads fixed-size chunks so memory use is independent of upload size.
    """

    def __init__(self, fileobj: BinaryReader, chunk_size: int) -> None:
        self._fileobj = fileobj
        self._chunk_size = chunk_size
        self._peeked: bytes | None = None

    def peek(self, size: int) -> bytes:
        data = self._fileobj.read(size)
        self._peeked = data
        return data

    def __iter__(self) -> Iterator[bytes]:
        if self._peeked:
            yield self._peeked
            self._peeked = None
        while True:
            chunk = self._fileobj.read(self._chunk_size)
            if not chunk:
                return
            yield chunk


def new_worker_id() -> str:
    return f"worker-{secrets.token_hex(6)}"


def now_utc() -> datetime:
    return datetime.now(UTC)
