"""Evidence-grounded research endpoints.

Every route requires an authenticated session. Answers are assembled from the
caller's own documents only; a citation carries the id of a document the caller
already owns, so it grants no new access.

The response is explicit about what it is. ``status`` distinguishes a grounded
answer from ``insufficient_evidence`` and ``no_evidence_available``, and each
citation carries a verbatim quotation plus whatever locators are actually known.
Missing metadata is omitted rather than filled in.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession

router = APIRouter(prefix="/research", tags=["research"])


class ResearchAskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=3, max_length=1000)


class CitationResponse(BaseModel):
    kind: str
    quote: str
    document_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    title: str | None = None
    page_number: int | None = None
    ordinal: int | None = None
    url: str | None = None
    locator: str
    score: float


class ResearchAnswerResponse(BaseModel):
    question: str
    language: str
    status: str
    evidence_sufficient: bool
    coverage: float
    explanation: str
    citations: list[CitationResponse]


class KnownSourceResponse(BaseModel):
    key: str
    title: str
    institution: str | None = None
    url: str | None = None
    language: str | None = None
    notes: str | None = None


class KnownSourceListResponse(BaseModel):
    items: list[KnownSourceResponse]
    # Retrieval is optional in MAFHUM; the client must be able to tell that a
    # source listed here has not actually been fetched.
    retrieval_enabled: bool


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(min_length=8, max_length=2048)


class RetrieveResponse(BaseModel):
    url: str
    ok: bool
    reason: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    truncated: bool = False
    # The sanitized excerpt is evidence, not proof of correctness. It is returned
    # so the user can judge the source, and because nothing may claim a source is
    # verified merely because it was reachable.
    excerpt: str = ""
    verification_status: str = "unverified"


@router.post("/ask", response_model=ResearchAnswerResponse, dependencies=[CsrfGuard])
def ask(
    payload: ResearchAskRequest, user: CurrentUser, context: Context, db: DbSession
) -> ResearchAnswerResponse:
    answer = context.research.ask(db, user.id, payload.question)
    return ResearchAnswerResponse(
        question=answer.question,
        language=answer.language,
        status=answer.status,
        evidence_sufficient=answer.evidence_sufficient,
        coverage=answer.coverage,
        explanation=answer.explanation,
        citations=[
            CitationResponse(
                kind=c.kind,
                quote=c.quote,
                document_id=c.document_id,
                source_id=c.source_id,
                title=c.title,
                page_number=c.page_number,
                ordinal=c.ordinal,
                url=c.url,
                locator=c.locator,
                score=c.score,
            )
            for c in answer.citations
        ],
    )


@router.get("/sources", response_model=KnownSourceListResponse)
def known_sources(context: Context) -> KnownSourceListResponse:
    from mafhum_api.services.research.retrieval import UnavailableFetcher

    return KnownSourceListResponse(
        items=[
            KnownSourceResponse(
                key=s.key,
                title=s.title,
                institution=s.institution,
                url=s.url,
                language=s.language,
                notes=s.notes,
            )
            for s in context.research.known_sources()
        ],
        retrieval_enabled=not isinstance(context.research.fetcher, UnavailableFetcher),
    )


@router.post("/retrieve", response_model=RetrieveResponse, dependencies=[CsrfGuard])
def retrieve(payload: RetrieveRequest, user: CurrentUser, context: Context) -> RetrieveResponse:
    page = context.research.retrieve(payload.url)
    return RetrieveResponse(
        url=page.url,
        ok=page.ok,
        reason=page.reason,
        status_code=page.status_code,
        content_type=page.content_type,
        final_url=page.final_url,
        truncated=page.truncated,
        excerpt=page.text[:2000],
        verification_status="unverified",
    )
