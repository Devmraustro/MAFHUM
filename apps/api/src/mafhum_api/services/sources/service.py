"""Academic source catalog metadata.

Anti-fabrication rules enforced here
------------------------------------
* Absent metadata stays ``NULL``. Nothing is defaulted to a plausible value.
* ``provenance`` records where data came from; ``verification_status`` is
  ``verified`` **only** when ``verified_by`` names who verified it.
* User-supplied values are recorded as ``user_asserted``, never as ``verified``.
* No network calls happen in this phase: no scraping, no resolver lookups, so no
  claim of external verification can be made implicitly.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mafhum_api.core.errors import AuthorizationError, NotFoundError, ValidationError
from mafhum_api.db.models import (
    Document,
    ProvenanceLevel,
    SourceRecord,
    SourceType,
    VerificationStatus,
)
from mafhum_api.services.sources.canonical import canonicalize_url

logger = logging.getLogger(__name__)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_ISBN_RE = re.compile(r"^(?:\d[\d\- ]{8,16}[\dXx])$")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_YEAR_MIN, _YEAR_MAX = 1000, 2100
_MAX_AUTHORS = 64


@dataclass(frozen=True, slots=True)
class SourceDraft:
    title: str | None = None
    authors: list[str] | None = None
    institution: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    source_type: str = SourceType.USER_UPLOAD.value
    doi: str | None = None
    isbn: str | None = None
    url: str | None = None
    repository_identifier: str | None = None
    language: str | None = None
    page_count: int | None = None
    notes: str | None = None
    document_id: uuid.UUID | None = None


class SourceValidationError(ValidationError):
    pass


class SourceService:
    def list_owned(
        self,
        db: Session,
        owner_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SourceRecord], int]:
        total = int(
            db.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.owner_id == owner_id)
            )
            or 0
        )
        rows = db.scalars(
            select(SourceRecord)
            .where(SourceRecord.owner_id == owner_id)
            .order_by(SourceRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), total

    def get_owned(self, db: Session, owner_id: uuid.UUID, source_id: uuid.UUID) -> SourceRecord:
        record = db.scalar(
            select(SourceRecord).where(
                SourceRecord.id == source_id, SourceRecord.owner_id == owner_id
            )
        )
        if record is None:
            raise AuthorizationError()
        return record

    def create(self, db: Session, *, owner_id: uuid.UUID, draft: SourceDraft) -> SourceRecord:
        values = self._clean(draft)
        if values.get("title") is None and values.get("url") is None and draft.document_id is None:
            raise SourceValidationError(
                "يجب إدخال عنوان أو رابط أو ربط المصدر بمستند.",
                field_errors={"title": ["مطلوب عنوان أو رابط أو مستند."]},
            )
        # ``document_id`` is resolved through the ownership check below, not
        # copied straight from the request.
        values.pop("document_id", None)
        document = self._attach_document(db, owner_id, draft.document_id)

        values["url_normalized"] = canonicalize_url(values.get("url"))
        record = SourceRecord(
            id=uuid.uuid4(),
            owner_id=owner_id,
            document_id=document.id if document else None,
            # Provenance: user-supplied unless it came from a parsed document.
            provenance=(
                ProvenanceLevel.DOCUMENT_EXTRACTED.value
                if document is not None and draft.title is None
                else ProvenanceLevel.USER_SUPPLIED.value
            ),
            verification_status=(
                VerificationStatus.USER_ASSERTED.value
                if any(
                    values.get(key) is not None
                    for key in ("title", "authors", "doi", "institution", "publication_year")
                )
                else VerificationStatus.UNVERIFIED.value
            ),
            **values,
        )
        db.add(record)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            # A clash on the canonical URL means a concurrent request already
            # catalogued this exact source. Hand back that record rather than
            # reporting an error: the caller's intent was satisfied.
            existing = self._find_by_canonical_url(db, owner_id, values.get("url_normalized"))
            if existing is not None:
                logger.info(
                    "source deduplicated",
                    extra={"event": "source_deduplicated", "source_id": str(existing.id)},
                )
                return existing
            raise SourceValidationError(
                "تعذّر حفظ المصدر؛ تحقق من صحة القيم.",
            ) from exc
        logger.info("source created", extra={"event": "source_created"})
        return record

    def _find_by_canonical_url(
        self, db: Session, owner_id: uuid.UUID, url_normalized: str | None
    ) -> SourceRecord | None:
        """Look up an owner's source by canonical URL identity.

        Scoped to the owner: another user's catalog of the same paper is theirs,
        and must never be returned here.
        """
        if not url_normalized:
            return None
        return db.scalar(
            select(SourceRecord).where(
                SourceRecord.owner_id == owner_id,
                SourceRecord.url_normalized == url_normalized,
            )
        )

    def update(
        self, db: Session, *, owner_id: uuid.UUID, source_id: uuid.UUID, draft: SourceDraft
    ) -> SourceRecord:
        record = self.get_owned(db, owner_id, source_id)
        values = self._clean(draft)
        for key, value in values.items():
            setattr(record, key, value)
        if "url" in values:
            # The derived identity must follow the URL, or a stale value would
            # keep deduplicating against the address this record no longer has.
            record.url_normalized = canonicalize_url(values["url"])
        if "document_id" in values:
            document = self._attach_document(db, owner_id, values["document_id"])
            record.document_id = document.id if document else None
        # Editing user metadata can never upgrade verification: an edit by the owner
        # is an assertion, not a verification.
        if record.verification_status == VerificationStatus.VERIFIED.value:
            record.verification_status = VerificationStatus.USER_ASSERTED.value
            record.verified_by = None
            record.verified_at = None
        db.flush()
        return record

    def delete(self, db: Session, *, owner_id: uuid.UUID, source_id: uuid.UUID) -> None:
        record = self.get_owned(db, owner_id, source_id)
        db.delete(record)
        db.flush()

    def create_from_document_metadata(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        document: Document,
        title: str | None,
        authors: Sequence[str] | None,
        page_count: int | None,
        language: str | None,
        parser_name: str | None,
        parser_version: str | None,
    ) -> SourceRecord:
        """Create a catalog entry from data *extracted from* the document.

        Values the parser did not find are left ``NULL`` rather than guessed.
        """
        record = SourceRecord(
            id=uuid.uuid4(),
            owner_id=owner_id,
            document_id=document.id,
            title=title,
            authors=list(authors) if authors else None,
            page_count=page_count,
            language=language,
            source_type=SourceType.USER_UPLOAD.value,
            provenance=ProvenanceLevel.DOCUMENT_EXTRACTED.value,
            verification_status=VerificationStatus.UNVERIFIED.value,
            parser_name=parser_name,
            parser_version=parser_version,
        )
        db.add(record)
        db.flush()
        return record

    # ------------------------------------------------------------------ helpers
    def _attach_document(
        self, db: Session, owner_id: uuid.UUID, document_id: uuid.UUID | None
    ) -> Document | None:
        if document_id is None:
            return None
        document = db.scalar(
            select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
        )
        if document is None:
            # Same message as a missing document: no cross-user existence oracle.
            raise SourceValidationError(
                "المستند المحدد غير متاح.", field_errors={"document_id": ["غير متاح."]}
            )
        return document

    def _clean(self, draft: SourceDraft) -> dict[str, Any]:
        """Validate and normalize user-supplied metadata. Rejects, never repairs."""
        errors: dict[str, list[str]] = {}
        values: dict[str, Any] = {}

        title = _opt_str(draft.title, 500)
        if title is not None:
            if not title.strip():
                errors["title"] = ["العنوان غير صالح."]
                title = None
            values["title"] = title
        institution = _opt_str(draft.institution, 255)
        if institution is not None:
            values["institution"] = institution
        publisher = _opt_str(draft.publisher, 255)
        if publisher is not None:
            values["publisher"] = publisher
        notes = _opt_str(draft.notes, 4000)
        if notes is not None:
            values["notes"] = notes
        repository_identifier = _opt_str(draft.repository_identifier, 255)
        if repository_identifier is not None:
            values["repository_identifier"] = repository_identifier

        if draft.source_type is not None:
            try:
                values["source_type"] = SourceType(draft.source_type).value
            except ValueError:
                errors["source_type"] = ["نوع المصدر غير معروف."]

        if draft.publication_year is not None:
            year = draft.publication_year
            if not isinstance(year, int) or not (_YEAR_MIN <= year <= _YEAR_MAX):
                errors["publication_year"] = [
                    f"سنة النشر يجب أن تكون بين {_YEAR_MIN} و{_YEAR_MAX}."
                ]
            else:
                values["publication_year"] = year

        if draft.page_count is not None:
            if draft.page_count <= 0:
                errors["page_count"] = ["عدد الصفحات يجب أن يكون أكبر من صفر."]
            else:
                values["page_count"] = draft.page_count

        if draft.doi is not None:
            doi = draft.doi.strip()
            if doi:
                if not _DOI_RE.match(doi):
                    errors["doi"] = ["معرّف DOI غير صالح. الصيغة: 10.xxxx/suffix"]
                else:
                    values["doi"] = doi[:255]

        if draft.isbn is not None:
            isbn = draft.isbn.strip()
            if isbn:
                if not _ISBN_RE.match(isbn):
                    errors["isbn"] = ["رقم ISBN غير صالح."]
                else:
                    values["isbn"] = isbn[:32]

        if draft.url is not None:
            url = draft.url.strip()
            if url:
                # Only http(s) is accepted, and the URL is stored as data. It is
                # never fetched in this phase, so there is no SSRF surface.
                if not _URL_RE.match(url):
                    errors["url"] = ["الرابط يجب أن يبدأ بـ http:// أو https://"]
                else:
                    values["url"] = url[:1024]

        if draft.language is not None:
            language = draft.language.strip()
            if language:
                if not _LANG_RE.match(language):
                    errors["language"] = ["رمز اللغة غير صالح."]
                else:
                    values["language"] = language[:8]

        if draft.authors is not None:
            authors = [a.strip()[:255] for a in draft.authors if isinstance(a, str) and a.strip()]
            if len(authors) > _MAX_AUTHORS:
                errors["authors"] = [f"الحد الأقصى {_MAX_AUTHORS} مؤلفًا."]
            else:
                # An empty list means "no authors known" -> keep it NULL, never "".
                values["authors"] = authors or None

        if draft.document_id is not None:
            values["document_id"] = draft.document_id

        if errors:
            raise SourceValidationError("تعذّر حفظ بيانات المصدر.", field_errors=errors)
        return values


def _opt_str(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value.strip()[:limit] or None


def summarize_source(record: SourceRecord) -> dict[str, Any]:
    """Serialize a source record for API responses."""
    return {
        "id": str(record.id),
        "title": record.title,
        "authors": record.authors,
        "institution": record.institution,
        "publisher": record.publisher,
        "publication_year": record.publication_year,
        "source_type": record.source_type,
        "doi": record.doi,
        "isbn": record.isbn,
        "url": record.url,
        "repository_identifier": record.repository_identifier,
        "language": record.language,
        "page_count": record.page_count,
        "provenance": record.provenance,
        "verification_status": record.verification_status,
        "verified_by": record.verified_by,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "document_id": str(record.document_id) if record.document_id else None,
        "parser_name": record.parser_name,
        "parser_version": record.parser_version,
        "notes": record.notes,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


__all__ = [
    "NotFoundError",
    "SourceDraft",
    "SourceService",
    "SourceValidationError",
    "summarize_source",
]
