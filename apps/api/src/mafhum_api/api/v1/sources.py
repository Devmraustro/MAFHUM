"""Academic source catalog endpoints.

This is a *catalog*, not a research engine. No endpoint here performs network
retrieval, so there is no SSRF surface, and nothing is ever marked ``verified``
unless a verifier was explicitly recorded.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession, Page, PageSize
from mafhum_api.api.schemas import (
    SourceCreateRequest,
    SourceListResponse,
    SourceResponse,
)
from mafhum_api.services.sources.service import SourceDraft

router = APIRouter(prefix="/sources", tags=["sources"])


def _to_draft(payload: SourceCreateRequest) -> SourceDraft:
    return SourceDraft(
        title=payload.title,
        authors=payload.authors,
        institution=payload.institution,
        publisher=payload.publisher,
        publication_year=payload.publication_year,
        source_type=payload.source_type,
        doi=payload.doi,
        isbn=payload.isbn,
        url=payload.url,
        repository_identifier=payload.repository_identifier,
        language=payload.language,
        page_count=payload.page_count,
        notes=payload.notes,
        document_id=payload.document_id,
    )


@router.get("", response_model=SourceListResponse)
def list_sources(
    user: CurrentUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
) -> SourceListResponse:
    records, total = context.sources.list_owned(db, user.id, page=page, page_size=page_size)
    return SourceListResponse(
        items=[SourceResponse.model_validate(r, from_attributes=True) for r in records],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CsrfGuard],
)
def create_source(
    payload: SourceCreateRequest,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> SourceResponse:
    record = context.sources.create(db, owner_id=user.id, draft=_to_draft(payload))
    db.commit()
    return SourceResponse.model_validate(record, from_attributes=True)


@router.get("/{source_id}", response_model=SourceResponse)
def get_source(
    source_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> SourceResponse:
    record = context.sources.get_owned(db, user.id, source_id)
    return SourceResponse.model_validate(record, from_attributes=True)


@router.patch(
    "/{source_id}",
    response_model=SourceResponse,
    dependencies=[CsrfGuard],
)
def update_source(
    source_id: uuid.UUID,
    payload: SourceCreateRequest,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> SourceResponse:
    record = context.sources.update(
        db, owner_id=user.id, source_id=source_id, draft=_to_draft(payload)
    )
    db.commit()
    return SourceResponse.model_validate(record, from_attributes=True)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[CsrfGuard],
)
def delete_source(
    source_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    response: Response,
) -> Response:
    context.sources.delete(db, owner_id=user.id, source_id=source_id)
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
