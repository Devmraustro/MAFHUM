"""Research orchestration: gather evidence, then ground an answer in it.

Authorization is enforced here rather than left to the caller. Evidence is
selected by ``owner_id`` on every query, so one student's documents can never
appear in another's answer — including through a citation, which carries the
document id a client could otherwise replay.

The service never calls a language model on the grounded path. Answers are
selections from ``DocumentTextSegment`` rows, so the strongest claim MAFHUM makes
is "this text appears at this location in a source you own", which is checkable.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mafhum_api.db.models import Document, DocumentStatus, DocumentTextSegment
from mafhum_api.services.research.grounding import Evidence, GroundedAnswer, ground_answer
from mafhum_api.services.research.retrieval import (
    KNOWN_SOURCES,
    RetrievedPage,
    SourceDescriptor,
    SourceFetcher,
    UnavailableFetcher,
)
from mafhum_api.services.research.sanitize import neutralize

logger = logging.getLogger(__name__)

MAX_EVIDENCE_SEGMENTS = 400
MAX_QUESTION_CHARS = 1000


class ResearchService:
    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        # Retrieval is optional; without an explicit fetcher nothing is fetched
        # and the reason is reported rather than silently invented.
        self._fetcher: SourceFetcher = fetcher or UnavailableFetcher()

    @property
    def fetcher(self) -> SourceFetcher:
        """The active fetcher, exposed so the API can report whether retrieval runs."""
        return self._fetcher

    # ------------------------------------------------------------------ answers
    def ask(self, db: Session, owner_id: uuid.UUID, question: str) -> GroundedAnswer:
        """Answer from the owner's own ready documents, or say evidence is lacking."""
        cleaned = neutralize(question or "")[:MAX_QUESTION_CHARS]
        evidence = self.document_evidence(db, owner_id)
        return ground_answer(cleaned, evidence)

    def document_evidence(
        self, db: Session, owner_id: uuid.UUID, *, limit: int = MAX_EVIDENCE_SEGMENTS
    ) -> list[Evidence]:
        """Every passage from this owner's successfully processed documents.

        Filtered on ownership and on ``ready`` status: a document that failed
        processing has no trustworthy text to cite, and a partially written one
        would be cited as though it were complete.
        """
        rows = db.execute(
            select(
                DocumentTextSegment.text,
                DocumentTextSegment.document_id,
                DocumentTextSegment.page_number,
                DocumentTextSegment.ordinal,
                Document.display_filename,
            )
            .join(Document, Document.id == DocumentTextSegment.document_id)
            .where(
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.READY.value,
            )
            .order_by(DocumentTextSegment.document_id, DocumentTextSegment.ordinal)
            .limit(limit)
        ).all()

        return [
            Evidence(
                kind="document",
                text=text,
                document_id=document_id,
                title=display_filename,
                page_number=page_number,
                ordinal=ordinal,
            )
            for text, document_id, page_number, ordinal, display_filename in rows
        ]

    # ------------------------------------------------------------------ sources
    def known_sources(self) -> tuple[SourceDescriptor, ...]:
        """The curated catalog. A missing URL means unverified, never guessed."""
        return KNOWN_SOURCES

    def retrieve(self, url: str) -> RetrievedPage:
        """Fetch one URL through the SSRF guard, reporting failure honestly."""
        page = self._fetcher.fetch(url)
        logger.info(
            "source retrieval",
            extra={
                "event": "source_retrieval",
                "ok": page.ok,
                "reason": page.reason,
                "status": page.status_code,
            },
        )
        return page

    def catalog_from_retrieval(
        self, page: RetrievedPage, *, title: str | None = None
    ) -> dict[str, object]:
        """Project a retrieval onto catalog metadata, leaving unknowns as None.

        Only ``url`` and the retrieval outcome are known for certain. A title is
        accepted only when the caller actually extracted one; the publication
        year, authors and DOI are never inferred from the page, so they stay
        ``None`` rather than being guessed from the HTML.
        """
        return {
            "url": page.final_url or page.url,
            "title": title,
            "provenance": "external_verified" if page.ok else "user_supplied",
            "verification_status": "unverified",
            "retrieved_at": page.retrieved_at or datetime.now(UTC),
            "retrieval_ok": page.ok,
            "retrieval_reason": page.reason,
        }
