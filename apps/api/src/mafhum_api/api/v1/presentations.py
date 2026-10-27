"""Editable presentation endpoints.

A deck can only be built from evidence the caller owns: either a fresh grounded
research answer or a saved research result whose citations still resolve. A
request with no supporting evidence is a 409, never a plausible-looking deck.
Artifacts are private blobs streamed through an ownership-checked endpoint.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mafhum_api.api.deps import (
    Context,
    CsrfGuard,
    CurrentUser,
    DbSession,
    Page,
    PageSize,
)
from mafhum_api.services.presentations.service import CreateDeckInput
from mafhum_api.services.presentations.validation import PPTX_MEDIA_TYPE

router = APIRouter(prefix="/presentations", tags=["presentations"])
artifact_router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_DISPOSITION_SAFE_RE = re.compile(r"[^ \w.\-\u0600-\u06ff\u00c0-\u024f]")


class PresentationCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    language: Literal["ar", "fr"] = "ar"
    question: str | None = Field(default=None, min_length=3, max_length=1000)
    saved_research_id: uuid.UUID | None = None


class PresentationCitationResponse(BaseModel):
    index: int
    locator: str
    title: str | None = None
    page_number: int | None = None
    available: bool


class PresentationArtifactResponse(BaseModel):
    id: str
    filename: str
    size_bytes: int


class PresentationJobResponse(BaseModel):
    id: str
    title: str
    question: str
    language: str
    status: str
    slide_count: int
    citation_count: int
    sources_available: bool
    citations: list[PresentationCitationResponse]
    generator: str | None = None
    error_code: str | None = None
    created_at: str | None = None
    artifact: PresentationArtifactResponse | None = None


class PresentationListResponse(BaseModel):
    items: list[PresentationJobResponse]
    total: int
    page: int
    page_size: int


class PresentationDeletedResponse(BaseModel):
    id: uuid.UUID
    deleted: bool


def _response(
    context: Context, db: DbSession, user: CurrentUser, job: object
) -> PresentationJobResponse:
    data = context.presentations.describe(db, user.id, job)  # type: ignore[arg-type]
    return PresentationJobResponse(
        id=data["id"],
        title=data["title"],
        question=data["question"],
        language=data["language"],
        status=data["status"],
        slide_count=data["slide_count"],
        citation_count=data["citation_count"],
        sources_available=data["sources_available"],
        citations=[PresentationCitationResponse(**item) for item in data["citations"]],
        generator=data["generator"],
        error_code=data["error_code"],
        created_at=data["created_at"],
        artifact=(PresentationArtifactResponse(**data["artifact"]) if data["artifact"] else None),
    )


@router.post(
    "/jobs",
    response_model=PresentationJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CsrfGuard],
)
def create_job(
    payload: PresentationCreateRequest,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> PresentationJobResponse:
    job = context.presentations.create_job(
        db,
        user.id,
        CreateDeckInput(
            title=payload.title,
            language=payload.language,
            question=payload.question,
            saved_research_id=payload.saved_research_id,
        ),
    )
    context.audit.record(
        db,
        action="presentation_created",
        actor_id=user.id,
        target_type="presentation",
        target_id=str(job.id),
        details={"count": job.slide_count},
    )
    db.commit()
    return _response(context, db, user, job)


@router.get("/jobs", response_model=PresentationListResponse)
def list_jobs(
    user: CurrentUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
) -> PresentationListResponse:
    jobs, total = context.presentations.list_jobs(db, user.id, page=page, page_size=page_size)
    return PresentationListResponse(
        items=[_response(context, db, user, job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=PresentationJobResponse)
def get_job(
    job_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> PresentationJobResponse:
    return _response(context, db, user, context.presentations.get_job(db, user.id, job_id))


@router.delete(
    "/jobs/{job_id}",
    response_model=PresentationDeletedResponse,
    dependencies=[CsrfGuard],
)
def delete_job(
    job_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> PresentationDeletedResponse:
    context.presentations.delete_job(db, user.id, job_id)
    context.audit.record(
        db,
        action="presentation_deleted",
        actor_id=user.id,
        target_type="presentation",
        target_id=str(job_id),
    )
    db.commit()
    return PresentationDeletedResponse(id=job_id, deleted=True)


@artifact_router.get("/{artifact_id}/download")
def download_artifact(
    artifact_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> StreamingResponse:
    """Stream a PPTX only after the owner has been verified in SQL."""
    stream, artifact = context.presentations.open_artifact(db, user.id, artifact_id)

    def chunks() -> Iterator[bytes]:
        yield from stream

    return StreamingResponse(
        chunks(),
        media_type=PPTX_MEDIA_TYPE,
        headers={
            "Content-Disposition": _content_disposition(artifact.display_filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _content_disposition(display_filename: str) -> str:
    normalized = unicodedata.normalize("NFC", display_filename)
    fallback = _DISPOSITION_SAFE_RE.sub("_", normalized.encode("ascii", "ignore").decode())
    fallback = fallback or "presentation.pptx"
    quoted = normalized.replace("\\", "").replace('"', "").replace("\n", "").replace("\r", "")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(quoted)}"


__all__ = ["artifact_router", "router"]
