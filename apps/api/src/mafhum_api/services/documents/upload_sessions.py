"""Chunked upload sessions for large documents.

Vercel Functions cap a single request body at 4.5 MB while the application
accepts documents up to 20 MB, and the pinned Python Blob SDK offers no way
to mint client-upload tokens (verified against ``vercel-blob==0.5.0``: only
the service-side ``client_token_pathname_mismatch`` error exists). The
compatible architecture is therefore API-mediated chunking:

1. ``create`` authenticates the user, validates the filename and declared
   content type against the same rules as the single-request path, checks
   quotas, and records an *intent*: owner, project, declared size, part
   size, part count, expiry, and a pre-allocated final storage key that the
   client never sees.
2. ``put_part`` accepts one bounded part per request (each stays under the
   platform body limit). Parts are stored under their own server-generated
   opaque keys; duplicate part numbers are refused; cumulative received
   bytes can never exceed the declared total.
3. ``finalize`` verifies that exactly the expected parts are present, streams
   them into the final object (no whole-file buffering), and hands the
   result to ``DocumentService.register_assembled_upload`` — the identical
   digest/size verification, structural validation, malware scanning, quota
   enforcement and job creation the single-request path applies. Nothing
   reaches the retrieval pipeline unscanned.
4. Expiry (on access, plus ``cleanup_expired`` for the reconciler) removes
   abandoned sessions and their stored parts.

The client can never substitute an object key: every key is server-generated
and finalize assembles exactly the parts recorded for the session.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mafhum_api.core.errors import (
    AuthorizationError,
    ConflictError,
    FileRejectedError,
    ValidationError,
)
from mafhum_api.db.models import (
    Document,
    DocumentUploadPart,
    DocumentUploadSession,
    UploadSessionState,
)
from mafhum_api.services.documents import validators
from mafhum_api.services.documents.service import DocumentLimits, DocumentService
from mafhum_api.services.documents.storage import (
    DocumentStorage,
    StorageError,
    StorageObjectNotFoundError,
)

logger = logging.getLogger(__name__)

_SIGNATURE_BYTES = 16
_CLEANUP_BATCH = 100


def _now() -> datetime:
    return datetime.now(UTC)


class UploadSessionService:
    """Owns the lifecycle of chunked uploads; authorization is per-request."""

    def __init__(
        self,
        storage: DocumentStorage,
        documents: DocumentService,
        limits: DocumentLimits,
        *,
        part_size_bytes: int,
        session_ttl: timedelta,
    ) -> None:
        if part_size_bytes <= 0:
            raise ValueError("part_size_bytes must be positive")
        self._storage = storage
        self._documents = documents
        self._limits = limits
        self._part_size = part_size_bytes
        self._ttl = session_ttl

    # ------------------------------------------------------------------ create
    def create_session(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        total_bytes: int,
        project_id: uuid.UUID | None = None,
        language_hint: str | None = None,
    ) -> DocumentUploadSession:
        """Validate and record an upload intent; nothing is stored yet."""
        display_name = validators.sanitize_display_name(filename)
        declared_format = validators.check_extension_and_mime(filename, content_type)

        if not isinstance(total_bytes, int) or total_bytes <= 0:
            raise ValidationError(
                "حجم الملف غير صالح.",
                field_errors={"total_bytes": ["غير صالح."]},
            )
        if total_bytes > self._limits.max_upload_bytes:
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_TOO_LARGE],
                details={"reason": validators.REASON_TOO_LARGE},
            )

        if project_id is not None:
            self._documents.assert_project_owned(db, owner_id, project_id)
        self._documents.soft_quota_check(db, owner_id)

        total_parts = (total_bytes + self._part_size - 1) // self._part_size
        session = DocumentUploadSession(
            id=uuid.uuid4(),
            owner_id=owner_id,
            project_id=project_id,
            storage_key=self._storage.generate_key(),
            display_filename=display_name,
            format=declared_format,
            declared_bytes=total_bytes,
            part_size=self._part_size,
            total_parts=total_parts,
            received_bytes=0,
            state=UploadSessionState.PENDING.value,
            language_hint=(language_hint or None),
            expires_at=_now() + self._ttl,
        )
        db.add(session)
        db.flush()
        return session

    # --------------------------------------------------------------- put part
    def put_part(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID,
        part_number: int,
        chunks: Iterable[bytes],
    ) -> DocumentUploadPart:
        """Store one bounded part of the session under a server-side key."""
        session = self._owned_session(db, owner_id, session_id)
        self._expire_if_due(db, session)

        if session.state != UploadSessionState.PENDING.value:
            raise ConflictError("انتهت صلاحية جلسة الرفع.")
        if not isinstance(part_number, int) or part_number < 1 or part_number > session.total_parts:
            raise ValidationError(
                "رقم الجزء غير صالح.",
                field_errors={"part_number": ["غير صالح."]},
            )

        existing = db.scalar(
            select(DocumentUploadPart).where(
                DocumentUploadPart.session_id == session.id,
                DocumentUploadPart.part_number == part_number,
            )
        )
        if existing is not None:
            # Duplicate protection: parts are immutable once accepted, so a
            # replayed request cannot swap bytes.
            raise ConflictError("تم استلام هذا الجزء مسبقاً.")

        expected_max = self.expected_part_size(session, part_number)
        remaining = session.declared_bytes - session.received_bytes
        if remaining <= 0:
            raise ConflictError("اكتمل الرفع بالفعل.")
        part_key = self._storage.generate_key()
        try:
            stored = self._storage.write(part_key, chunks, max_bytes=expected_max)
        except StorageError as exc:
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_TOO_LARGE],
                details={"reason": validators.REASON_TOO_LARGE},
            ) from exc
        if stored.size_bytes == 0:
            self._compensate_part(part_key)
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_EMPTY],
                details={"reason": validators.REASON_EMPTY},
            )

        try:
            part = DocumentUploadPart(
                id=uuid.uuid4(),
                session_id=session.id,
                part_number=part_number,
                storage_key=part_key,
                size_bytes=stored.size_bytes,
            )
            db.add(part)
            session.received_bytes += stored.size_bytes
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            self._compensate_part(part_key)
            raise
        return part

    @staticmethod
    def expected_part_size(session: DocumentUploadSession, part_number: int) -> int:
        """Maximum bytes part ``part_number`` may carry."""
        full_parts_bytes = (session.total_parts - 1) * session.part_size
        remainder = session.declared_bytes - full_parts_bytes
        return session.part_size if part_number < session.total_parts else max(remainder, 1)

    # --------------------------------------------------------------- finalize
    def finalize(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> Document:
        """Assemble the parts and register the document through the normal
        validation, scanning, quota, and job pipeline."""
        session = self._owned_session(db, owner_id, session_id)
        self._expire_if_due(db, session)

        if session.state != UploadSessionState.PENDING.value:
            raise ConflictError("انتهت صلاحية جلسة الرفع.")

        parts = list(
            db.scalars(
                select(DocumentUploadPart)
                .where(DocumentUploadPart.session_id == session.id)
                .order_by(DocumentUploadPart.part_number)
            )
        )
        received = sum(part.size_bytes for part in parts)
        if len(parts) != session.total_parts or received != session.declared_bytes:
            raise ConflictError(
                "الرفع غير مكتمل.",
                details={
                    "received_parts": len(parts),
                    "expected_parts": session.total_parts,
                    "received_bytes": received,
                    "declared_bytes": session.declared_bytes,
                },
            )

        head = self._read_head(parts)
        # Assemble: stream the parts, in order, into the final object under
        # the pre-allocated session key. max_bytes equals the declared total,
        # so a mismatch between parts and declaration can never publish more
        # than the accepted size.
        try:
            self._storage.write(
                session.storage_key,
                self._part_stream(parts),
                max_bytes=session.declared_bytes,
            )
        except StorageError as exc:
            raise FileRejectedError(
                validators.ARABIC_MESSAGES[validators.REASON_MALFORMED],
                details={"reason": validators.REASON_MALFORMED},
            ) from exc

        try:
            document = self._documents.register_assembled_upload(
                db,
                owner_id=owner_id,
                project_id=session.project_id,
                storage_key=session.storage_key,
                display_name=session.display_filename,
                declared_format=session.format,
                head=head,
                language_hint=session.language_hint,
            )
        except BaseException:
            # Registration failed (validation, scan, quota, database). The
            # assembled object must not survive, exactly like the
            # single-request path compensates its blob — and neither may the
            # parts: on a deterministic rejection (malware, malformed, size)
            # the stored chunks are the rejected bytes themselves.
            self._compensate_part(session.storage_key)
            # The registration path may have rolled the transaction back;
            # the expiry below re-persists the session state and deletes the
            # part objects, so storage and database agree.
            db.rollback()
            self._expire_session(db, session)
            raise

        try:
            session.state = UploadSessionState.ASSEMBLED.value
            db.flush()
        except SQLAlchemyError:
            db.rollback()
            raise
        # The parts have been assembled into the final object; they are pure
        # orphans now. Best-effort removal, report-only on failure.
        _deleted, failed = self._storage.delete_many([part.storage_key for part in parts])
        if failed:
            logger.error(
                "upload part cleanup left orphans; reconcile required",
                extra={"event": "upload_parts_cleanup_failed", "count": len(failed)},
            )
        logger.info(
            "chunked upload assembled",
            extra={
                "event": "upload_session_assembled",
                "document_id": str(document.id),
                "parts": len(parts),
            },
        )
        return document

    # ------------------------------------------------------------------ abort
    def abort(self, db: Session, *, owner_id: uuid.UUID, session_id: uuid.UUID) -> None:
        """Cancel a pending session and remove its stored parts."""
        session = self._owned_session(db, owner_id, session_id)
        if session.state == UploadSessionState.ASSEMBLED.value:
            raise ConflictError("تم إكمال الجلسة بالفعل.")
        self._expire_session(db, session)

    # --------------------------------------------------------------- cleanup
    def cleanup_expired(self, db: Session, *, now: datetime | None = None) -> int:
        """Expire overdue sessions and delete their stored parts.

        Idempotent and bounded: safe to call repeatedly (from a reconciler or
        the durable-processing tick) — already-expired sessions are skipped.
        """
        moment = now or _now()
        sessions = list(
            db.scalars(
                select(DocumentUploadSession)
                .where(
                    DocumentUploadSession.state == UploadSessionState.PENDING.value,
                    DocumentUploadSession.expires_at < moment,
                )
                .limit(_CLEANUP_BATCH)
            )
        )
        for session in sessions:
            self._expire_session(db, session)
        return len(sessions)

    # ----------------------------------------------------------------- shared
    def _owned_session(
        self, db: Session, owner_id: uuid.UUID, session_id: uuid.UUID
    ) -> DocumentUploadSession:
        session = db.get(DocumentUploadSession, session_id)
        if session is None or session.owner_id != owner_id:
            # Same error for missing and not-owned: no existence oracle.
            raise AuthorizationError()
        return session

    def _expire_if_due(self, db: Session, session: DocumentUploadSession) -> None:
        if session.state == UploadSessionState.PENDING.value and session.expires_at < _now():
            self._expire_session(db, session)

    def _expire_session(self, db: Session, session: DocumentUploadSession) -> None:
        parts = list(
            db.scalars(
                select(DocumentUploadPart).where(DocumentUploadPart.session_id == session.id)
            )
        )
        if parts:
            _deleted, failed = self._storage.delete_many([part.storage_key for part in parts])
            if failed:
                logger.error(
                    "expired upload left orphan parts; reconcile required",
                    extra={
                        "event": "upload_expiry_cleanup_failed",
                        "count": len(failed),
                    },
                )
        session.state = UploadSessionState.EXPIRED.value
        # Commit immediately: the stored parts are already physically gone
        # (storage is not transactional), so the database must not keep
        # claiming a live pending session — even when the request that
        # triggered the expiry then fails.
        db.commit()

    def _read_head(self, parts: Sequence[DocumentUploadPart]) -> bytes:
        """First bytes of the first part, for magic-number detection."""
        buffer = bytearray()
        for chunk in self._storage.open_read(parts[0].storage_key):
            buffer.extend(chunk)
            if len(buffer) >= _SIGNATURE_BYTES:
                break
        return bytes(buffer[:_SIGNATURE_BYTES])

    def _compensate_part(self, key: str) -> None:
        try:
            self._storage.delete(key)
        except (StorageError, StorageObjectNotFoundError):
            logger.error(
                "could not remove stored upload part; reconcile required",
                extra={"event": "upload_part_compensation_failed"},
            )

    def _part_stream(self, parts: Sequence[DocumentUploadPart]) -> Iterator[bytes]:
        for part in parts:
            yield from self._storage.open_read(part.storage_key)
