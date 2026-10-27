"""Document endpoints under ``/api/v1/documents``.

Authorization happens in the service layer, in the SQL predicate, before any
bytes are read. Error responses for another user's document are identical to
those for a nonexistent one, so the API is not an existence oracle.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from mafhum_api.api.deps import (
    Context,
    CsrfGuard,
    CurrentUser,
    DbSession,
    Page,
    PageSize,
)
from mafhum_api.api.schemas import (
    DocumentDeletedResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentRetryResponse,
    DocumentTextResponse,
    DocumentTextSegmentResponse,
    LimitsResponse,
    UsageResponse,
)
from mafhum_api.core.errors import ValidationError
from mafhum_api.db.models import Document
from mafhum_api.services.documents.validators import ALLOWED_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_DISPOSITION_SAFE_RE = re.compile(r"[^ \w.\-\u0600-\u06ff\u00c0-\u024f]")

_DOWNLOAD_CHUNK = 1024 * 128


def _to_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        display_filename=document.display_filename,
        format=document.format,  # type: ignore[arg-type]
        size_bytes=document.size_bytes,
        status=document.status,  # type: ignore[arg-type]
        page_count=document.page_count,
        char_count=document.char_count,
        segment_count=document.segment_count,
        project_id=document.project_id,
        language_hint=document.language_hint,
        is_scanned=document.is_scanned,
        parser_name=document.parser_name,
        parser_version=document.parser_version,
        error_code=document.error_code,
        error_message=document.error_message,
        created_at=document.created_at,
        processed_at=document.processed_at,
    )


@router.get("/limits", response_model=LimitsResponse)
def upload_limits(context: Context) -> LimitsResponse:
    """Expose the server's real limits so the UI never has to guess them."""
    settings = context.settings
    return LimitsResponse(
        max_upload_mb=settings.max_upload_mb,
        max_pdf_pages=settings.max_pdf_pages,
        max_documents_per_user=settings.max_documents_per_user,
        max_total_bytes_per_user_mb=settings.max_total_bytes_per_user_mb,
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        ocr_available=False,
        # Read from the service that does the scanning, so what is advertised is
        # what actually happens rather than a constant that can drift.
        malware_scanning_available=context.documents.scanner.available,
    )


@router.get("/usage", response_model=UsageResponse)
def usage(user: CurrentUser, context: Context, db: DbSession) -> UsageResponse:
    count, total_bytes = context.documents.storage_usage(db, user.id)
    settings = context.settings
    return UsageResponse(
        documents=count,
        total_bytes=total_bytes,
        max_documents=settings.max_documents_per_user,
        max_total_bytes=settings.max_total_bytes_per_user,
    )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CsrfGuard],
)
async def upload_document(
    user: CurrentUser,
    context: Context,
    db: DbSession,
    file: Annotated[UploadFile, File(description="ملف PDF أو DOCX")],
    project_id: Annotated[str | None, Form()] = None,
) -> DocumentResponse:
    """Upload a PDF or DOCX. Processing starts asynchronously."""
    parsed_project_id: uuid.UUID | None = None
    if project_id:
        try:
            parsed_project_id = uuid.UUID(project_id)
        except ValueError as exc:
            raise ValidationError(
                "المشروع المحدد غير متاح.", field_errors={"project_id": ["غير متاح."]}
            ) from exc

    # Reading the spooled upload inside a worker thread keeps the event loop free
    # and bounds memory: the service consumes fixed-size chunks, never the whole
    # file at once.
    document = await run_in_threadpool(
        context.documents.upload,
        db,
        owner_id=user.id,
        filename=file.filename or "untitled",
        content_type=file.content_type,
        fileobj=file.file,
        project_id=parsed_project_id,
    )
    context.audit.record(
        db,
        action="document_uploaded",
        actor_id=user.id,
        target_type="document",
        target_id=str(document.id),
    )
    db.commit()
    # Nudge the worker instead of waiting for its next poll.
    context.worker.wake()
    return _to_response(document)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    user: CurrentUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
    project_id: str | None = None,
    status_filter: str | None = None,
) -> DocumentListResponse:
    parsed_project_id: uuid.UUID | None = None
    if project_id:
        try:
            parsed_project_id = uuid.UUID(project_id)
        except ValueError as exc:
            raise ValidationError(
                "المشروع المحدد غير متاح.", field_errors={"project_id": ["غير متاح."]}
            ) from exc
    status_value = (
        status_filter if status_filter in {"pending", "processing", "ready", "failed"} else None
    )
    documents, total = context.documents.list_owned(
        db,
        user.id,
        page=page,
        page_size=page_size,
        project_id=parsed_project_id,
        status=status_value,
    )
    return DocumentListResponse(
        items=[_to_response(d) for d in documents],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> DocumentResponse:
    document = context.documents.get_owned(db, user.id, document_id)
    return _to_response(document)


@router.get("/{document_id}/text", response_model=DocumentTextResponse)
def get_document_text(
    document_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
) -> DocumentTextResponse:
    result = context.documents.get_text(db, user.id, document_id, page=page, page_size=page_size)
    return DocumentTextResponse(
        document_id=document_id,
        status=result.document_status,
        items=[
            DocumentTextSegmentResponse(
                ordinal=ordinal,
                page_number=page_number,
                text=text,
                char_count=len(text),
            )
            for ordinal, _segment_id, page_number, text in result.segments
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/{document_id}/download")
def download_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> StreamingResponse:
    """Stream the original file after ownership has been verified.

    The response is attachment-only with a sanitized filename; the storage key and
    any filesystem path are never exposed.
    """
    stream, document = context.documents.open_stream(db, user.id, document_id)

    def iter_chunks() -> Iterator[bytes]:
        yield from stream

    media_type = (
        "application/pdf"
        if document.format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return StreamingResponse(
        iter_chunks(),
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(document.display_filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/{document_id}/retry",
    response_model=DocumentRetryResponse,
    dependencies=[CsrfGuard],
)
def retry_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> DocumentRetryResponse:
    job = context.documents.retry(db, user.id, document_id)
    db.commit()
    context.worker.wake()
    return DocumentRetryResponse(document_id=document_id, job_id=job.id, status=job.status)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeletedResponse,
    dependencies=[CsrfGuard],
)
def delete_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> DocumentDeletedResponse:
    context.documents.delete(db, user.id, document_id)
    context.audit.record(
        db,
        action="document_deleted",
        actor_id=user.id,
        target_type="document",
        target_id=str(document_id),
    )
    db.commit()
    return DocumentDeletedResponse(id=document_id, deleted=True)


def _content_disposition(display_filename: str) -> str:
    """Build an RFC 5987 disposition that cannot break out of the header."""
    normalized = unicodedata.normalize("NFC", display_filename)
    ascii_fallback = _DISPOSITION_SAFE_RE.sub("_", normalized.encode("ascii", "ignore").decode())
    ascii_fallback = ascii_fallback or "document"
    quoted = normalized.replace("\\", "").replace('"', "").replace("\n", "").replace("\r", "")
    from urllib.parse import quote

    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(quoted)}"


__all__ = ["router"]
