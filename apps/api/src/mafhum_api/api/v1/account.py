"""Account self-service: usage summary, data export and account deletion."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession
from mafhum_api.api.schemas import AccountDeletionResponse, UsageResponse
from mafhum_api.api.v1.auth import _clear_auth_cookies
from mafhum_api.services.account.export import (
    AccountExportService,
    ExportLimits,
    stream_chunks,
)

router = APIRouter(tags=["account"])


@router.get("/me/usage", response_model=UsageResponse)
def my_usage(user: CurrentUser, context: Context, db: DbSession) -> UsageResponse:
    count, total_bytes = context.documents.storage_usage(db, user.id)
    settings = context.settings
    return UsageResponse(
        documents=count,
        total_bytes=total_bytes,
        max_documents=settings.max_documents_per_user,
        max_total_bytes=settings.max_total_bytes_per_user,
    )


@router.get("/me/export")
def export_my_data(
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> StreamingResponse:
    """Stream a ZIP of the caller's own data, built under hard caps.

    Session-authenticated (no bearer/API token), GET-only, and never cached.
    Ownership filtering happens in SQL inside the export service; the archive
    is assembled before the first byte is sent, so a failure produces a normal
    error envelope instead of a truncated download that looks successful.
    """
    settings = context.settings
    service = AccountExportService(
        context.storage,
        ExportLimits(
            max_files=settings.export_max_files,
            max_file_bytes=settings.export_max_file_bytes,
            max_total_bytes=settings.export_max_total_bytes,
            spool_memory_bytes=settings.export_spool_memory_mb * 1024 * 1024,
            max_seconds=settings.export_max_seconds,
        ),
    )
    result = service.build(db, user)
    context.audit.record(
        db,
        action="account_exported",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        details={"count": result.file_count, "status": "truncated" if result.truncated else "ok"},
    )
    db.commit()

    filename = f"mafhum-export-{datetime.now(UTC).date().isoformat()}.zip"
    return StreamingResponse(
        stream_chunks(result),
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(result.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "X-MAFHUM-Export-Truncated": "true" if result.truncated else "false",
        },
    )


@router.delete(
    "/me",
    response_model=AccountDeletionResponse,
    dependencies=[CsrfGuard],
)
def delete_account(
    request: Request,
    response: Response,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> AccountDeletionResponse:
    """Delete the account and all of its data.

    Access is revoked before anything is removed. If a stored file cannot be
    deleted, the account stays tombstoned (so it remains inaccessible) and the
    response reports the work still outstanding rather than claiming success.
    """
    # Record before the account row goes away; the foreign key then sets
    # actor_id to NULL, preserving the event without a dangling reference.
    context.audit.record(
        db,
        action="account_deleted",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    report = context.deletion.delete_account(db, user.id)
    db.commit()
    _clear_auth_cookies(response, context)
    return AccountDeletionResponse(
        deleted=report.complete,
        complete=report.complete,
        documents_removed=report.documents_removed,
        blobs_removed=report.blobs_removed,
        blobs_pending=len(report.blobs_failed),
        detail=None if report.complete else {"pending": "blob_deletion_incomplete"},
    )
