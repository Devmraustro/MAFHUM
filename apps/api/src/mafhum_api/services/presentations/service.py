"""Presentation jobs, private artifacts and ownership enforcement.

Generation is synchronous in this phase. That is a deliberate, documented
deviation from the architecture's "bounded background jobs" preference (ADR
0005): with a pure-Python generator and a structurally bounded spec, a deck is
produced in well under a second, so a queue would add a broker, another process
and a second failure mode without a measured benefit. The work is still bounded
by caps on job count, citation count, slide count, text length and output bytes,
and the ``DeckGenerator`` interface is what a future worker would call.

Authorization lives in the SQL predicate on every read, download and delete. A
resource belonging to another user returns the same 404 as a missing one.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mafhum_api.core.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    QuotaExceededError,
    ValidationError,
)
from mafhum_api.db.models import (
    Document,
    DocumentStatus,
    JobStatus,
    PresentationArtifact,
    PresentationJob,
    SavedResearch,
    SourceRecord,
)
from mafhum_api.services.documents.storage import DocumentStorage, StorageError
from mafhum_api.services.presentations.builder import build_deck_spec
from mafhum_api.services.presentations.generator import DeckGenerator, describe_file
from mafhum_api.services.presentations.spec import (
    DeckCitation,
    DeckSpec,
    SpecError,
)
from mafhum_api.services.presentations.validation import validate
from mafhum_api.services.research.service import ResearchService

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 128


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PresentationLimits:
    max_jobs_per_user: int
    max_citations: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class CreateDeckInput:
    title: str
    language: str = "ar"
    question: str | None = None
    saved_research_id: uuid.UUID | None = None


def _as_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _locator(raw: dict[str, Any], title: str | None, page: int | None, ordinal: int | None) -> str:
    explicit = raw.get("locator")
    if isinstance(explicit, str) and explicit.strip():
        return explicit[:300]
    parts = [
        part
        for part in (title, f"p. {page}" if page else None, f"§{ordinal}" if ordinal else None)
        if part
    ]
    return " — ".join(parts)[:300]


def _normalize_citation(raw: dict[str, Any], index: int) -> DeckCitation:
    title = _as_str(raw.get("title"))
    page = _as_int(raw.get("page_number"))
    ordinal = _as_int(raw.get("ordinal"))
    return DeckCitation(
        index=index,
        quote=str(raw.get("quote") or "")[:900],
        locator=_locator(raw, title, page, ordinal),
        title=title[:200] if title else None,
        page_number=page,
        ordinal=ordinal,
        url=_as_str(raw.get("url")),
        document_id=_as_str(raw.get("document_id")),
        source_id=_as_str(raw.get("source_id")),
        available=False,  # recomputed against the database before any use
    )


class PresentationService:
    def __init__(
        self,
        storage: DocumentStorage,
        generator: DeckGenerator,
        research: ResearchService,
        limits: PresentationLimits,
    ) -> None:
        self._storage = storage
        self._generator = generator
        self._research = research
        self._limits = limits

    # ------------------------------------------------------------------ create
    def create_job(
        self, db: Session, owner_id: uuid.UUID, payload: CreateDeckInput
    ) -> PresentationJob:
        if payload.language not in ("ar", "fr"):
            raise ValidationError("لغة العرض غير مدعومة.", field_errors={"language": ["ar أو fr"]})
        if not payload.question and payload.saved_research_id is None:
            raise ValidationError(
                "يجب تقديم سؤال أو نتيجة بحث محفوظة.",
                field_errors={"question": ["question or saved_research_id is required"]},
            )

        question, raw_citations = self._evidence(db, owner_id, payload)
        if not raw_citations:
            error = ConflictError("لا توجد أدلة كافية لبناء عرض موثّق.")
            error.code = "insufficient_evidence"
            raise error

        jobs = (
            db.scalar(
                select(func.count())
                .select_from(PresentationJob)
                .where(PresentationJob.owner_id == owner_id)
            )
            or 0
        )
        if jobs >= self._limits.max_jobs_per_user:
            raise QuotaExceededError("بلغت الحد الأقصى لعدد العروض.")

        citations = self._verified(db, owner_id, raw_citations[: self._limits.max_citations])
        try:
            spec = build_deck_spec(
                title=payload.title,
                question=question,
                citations=citations,
                language=payload.language,
            )
        except SpecError as exc:
            raise ValidationError("تعذّر بناء العرض من هذه الأدلة.") from exc

        job = PresentationJob(
            owner_id=owner_id,
            title=spec.title,
            question=question[:1000],
            language=payload.language,
            status=JobStatus.RUNNING.value,
            spec=spec.to_dict(),
            generator_name=self._generator.name,
            generator_version=self._generator.version,
        )
        db.add(job)
        db.flush()

        try:
            with tempfile.TemporaryDirectory(prefix="mafhum-deck-") as tmp:
                output = Path(tmp) / "deck.pptx"
                self._generator.generate(spec, output)
                validate(
                    output,
                    expected_slides=len(spec.slides),
                    require_rtl=spec.rtl,
                    require_chart=True,
                    require_table=True,
                    max_bytes=self._limits.max_output_bytes,
                )
                info = describe_file(output)
                key = self._storage.generate_key()
                with output.open("rb") as handle:
                    self._storage.write(
                        key,
                        iter(lambda: handle.read(_CHUNK), b""),
                        max_bytes=self._limits.max_output_bytes,
                    )

            artifact = PresentationArtifact(
                owner_id=owner_id,
                job_id=job.id,
                storage_key=key,
                display_filename=_safe_filename(spec.title),
                size_bytes=info.size_bytes,
                content_sha256=info.sha256,
            )
            db.add(artifact)
            job.status = JobStatus.SUCCEEDED.value
            job.slide_count = len(spec.slides)
            job.citation_count = len(spec.citations)
            job.finished_at = _utcnow()
            db.flush()
            logger.info("presentation generated", extra={"event": "presentation_succeeded"})
            return job
        except Exception as exc:
            # No exception text reaches the client or the database: it can carry
            # temporary paths. The failure is durable so the UI can show it.
            logger.error("presentation generation failed", extra={"event": "presentation_failed"})
            job.status = JobStatus.FAILED.value
            job.error_code = "generation_failed"
            job.error_message = "generation_failed"
            job.finished_at = _utcnow()
            db.commit()
            failure = DomainError("تعذّر إنشاء ملف العرض. حاول مجددًا.")
            failure.code = "deck_generation_failed"
            failure.status_code = 503  # type: ignore[assignment]
            raise failure from exc

    # ---------------------------------------------------------------- evidence
    def _evidence(
        self, db: Session, owner_id: uuid.UUID, payload: CreateDeckInput
    ) -> tuple[str, list[dict[str, Any]]]:
        if payload.saved_research_id is not None:
            saved = db.scalar(
                select(SavedResearch).where(
                    SavedResearch.id == payload.saved_research_id,
                    SavedResearch.owner_id == owner_id,
                )
            )
            if saved is None:
                raise NotFoundError()
            if saved.status != "grounded" or not saved.evidence_sufficient:
                error = ConflictError("نتيجة البحث المحفوظة غير مدعومة بأدلة كافية.")
                error.code = "insufficient_evidence"
                raise error
            rows = saved.citations if isinstance(saved.citations, list) else []
            return saved.question, [dict(row) for row in rows if isinstance(row, dict)]

        question = payload.question
        if question is None:
            raise ValidationError("يجب تقديم سؤال أو نتيجة بحث محفوظة.")
        answer = self._research.ask(db, owner_id, question)
        if answer.status != "grounded" or not answer.evidence_sufficient:
            error = ConflictError("لا توجد أدلة كافية لبناء عرض موثّق.")
            error.code = "insufficient_evidence"
            raise error
        raw: list[dict[str, Any]] = [
            {
                "kind": c.kind,
                "quote": c.quote,
                "document_id": str(c.document_id) if c.document_id else None,
                "source_id": str(c.source_id) if c.source_id else None,
                "title": c.title,
                "page_number": c.page_number,
                "ordinal": c.ordinal,
                "url": c.url,
                "locator": c.locator,
                "score": c.score,
            }
            for c in answer.citations
        ]
        return answer.question, raw

    def _available_ids(
        self, db: Session, owner_id: uuid.UUID, citations: list[DeckCitation]
    ) -> tuple[set[str], set[str]]:
        document_ids = {c.document_id for c in citations if c.document_id}
        source_ids = {c.source_id for c in citations if c.source_id}
        documents: set[str] = set()
        sources: set[str] = set()
        parsed_documents = _parse_uuids(document_ids)
        parsed_sources = _parse_uuids(source_ids)
        if parsed_documents:
            rows = db.execute(
                select(Document.id).where(
                    Document.owner_id == owner_id,
                    Document.status == DocumentStatus.READY.value,
                    Document.id.in_(parsed_documents),
                )
            ).scalars()
            documents = {str(row) for row in rows}
        if parsed_sources:
            rows = db.execute(
                select(SourceRecord.id).where(
                    SourceRecord.owner_id == owner_id,
                    SourceRecord.id.in_(parsed_sources),
                )
            ).scalars()
            sources = {str(row) for row in rows}
        return documents, sources

    def _verified(
        self, db: Session, owner_id: uuid.UUID, raw: list[dict[str, Any]]
    ) -> list[DeckCitation]:
        normalized = [_normalize_citation(item, index) for index, item in enumerate(raw, start=1)]
        documents, sources = self._available_ids(db, owner_id, normalized)
        verified: list[DeckCitation] = []
        for citation in normalized:
            available = bool(
                (citation.document_id and citation.document_id in documents)
                or (citation.source_id and citation.source_id in sources)
            )
            if not available:
                error = ConflictError("أحد المصادر لم يعد متاحًا. لا يمكن إنشاء عرض يستشهد به.")
                error.code = "source_unavailable"
                raise error
            verified.append(
                DeckCitation(
                    index=citation.index,
                    quote=citation.quote,
                    locator=citation.locator,
                    title=citation.title,
                    page_number=citation.page_number,
                    ordinal=citation.ordinal,
                    url=citation.url,
                    document_id=citation.document_id,
                    source_id=citation.source_id,
                    available=True,
                )
            )
        return verified

    # ------------------------------------------------------------------- reads
    def get_job(self, db: Session, owner_id: uuid.UUID, job_id: uuid.UUID) -> PresentationJob:
        job = db.scalar(
            select(PresentationJob).where(
                PresentationJob.id == job_id,
                PresentationJob.owner_id == owner_id,
            )
        )
        if job is None:
            raise NotFoundError()
        return job

    def list_jobs(
        self, db: Session, owner_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[PresentationJob], int]:
        total = (
            db.scalar(
                select(func.count())
                .select_from(PresentationJob)
                .where(PresentationJob.owner_id == owner_id)
            )
            or 0
        )
        rows = list(
            db.scalars(
                select(PresentationJob)
                .where(PresentationJob.owner_id == owner_id)
                .order_by(PresentationJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def artifact_for_job(
        self, db: Session, owner_id: uuid.UUID, job_id: uuid.UUID
    ) -> PresentationArtifact | None:
        return db.scalar(
            select(PresentationArtifact).where(
                PresentationArtifact.job_id == job_id,
                PresentationArtifact.owner_id == owner_id,
            )
        )

    def describe(self, db: Session, owner_id: uuid.UUID, job: PresentationJob) -> dict[str, Any]:
        """Project a job into a response, recomputing source availability."""
        spec: DeckSpec | None = None
        if isinstance(job.spec, dict) and job.spec:
            try:
                spec = DeckSpec.from_dict(dict(job.spec))
            except (SpecError, KeyError, TypeError):
                spec = None
        citations: list[dict[str, Any]] = []
        sources_available = True
        if spec is not None:
            documents, sources = self._available_ids(db, owner_id, list(spec.citations))
            for citation in spec.citations:
                available = bool(
                    (citation.document_id and citation.document_id in documents)
                    or (citation.source_id and citation.source_id in sources)
                )
                sources_available = sources_available and available
                citations.append(
                    {
                        "index": citation.index,
                        "locator": citation.locator,
                        "title": citation.title,
                        "page_number": citation.page_number,
                        "available": available,
                    }
                )
        artifact = self.artifact_for_job(db, owner_id, job.id)
        return {
            "id": str(job.id),
            "title": job.title,
            "question": job.question,
            "language": job.language,
            "status": job.status,
            "slide_count": job.slide_count,
            "citation_count": job.citation_count,
            "sources_available": sources_available,
            "citations": citations,
            "generator": job.generator_name,
            "error_code": job.error_code,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "artifact": (
                {
                    "id": str(artifact.id),
                    "filename": artifact.display_filename,
                    "size_bytes": artifact.size_bytes,
                }
                if artifact is not None
                else None
            ),
        }

    # --------------------------------------------------------------- downloads
    def open_artifact(
        self, db: Session, owner_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> tuple[Iterator[bytes], PresentationArtifact]:
        artifact = db.scalar(
            select(PresentationArtifact).where(
                PresentationArtifact.id == artifact_id,
                PresentationArtifact.owner_id == owner_id,
            )
        )
        if artifact is None:
            raise NotFoundError()
        return self._storage.open_read(artifact.storage_key), artifact

    # ---------------------------------------------------------------- deletion
    def delete_job(self, db: Session, owner_id: uuid.UUID, job_id: uuid.UUID) -> None:
        job = self.get_job(db, owner_id, job_id)
        artifact = self.artifact_for_job(db, owner_id, job.id)
        if artifact is not None:
            try:
                self._storage.delete(artifact.storage_key)
            except StorageError as exc:
                error = DomainError("تعذّر حذف ملف العرض. حاول مجددًا.")
                error.code = "storage_error"
                raise error from exc
            db.delete(artifact)
        db.delete(job)
        db.flush()

    def artifact_keys_for_owner(self, db: Session, owner_id: uuid.UUID) -> list[str]:
        """Keys an account-deletion reclaim must remove before dropping rows."""
        return list(
            db.scalars(
                select(PresentationArtifact.storage_key).where(
                    PresentationArtifact.owner_id == owner_id
                )
            ).all()
        )


def _parse_uuids(values: set[str]) -> list[uuid.UUID]:
    """Only well-formed ids may reach a UUID column in an IN clause."""
    parsed: list[uuid.UUID] = []
    for value in values:
        try:
            parsed.append(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return parsed


def _safe_filename(title: str) -> str:
    """A deterministic, ASCII-safe download name. The real title stays Arabic."""
    return "mafhum-deck.pptx"


__all__ = [
    "CreateDeckInput",
    "PresentationLimits",
    "PresentationService",
]
