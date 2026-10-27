"""Account self-service: usage summary and account deletion."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession
from mafhum_api.api.schemas import AccountDeletionResponse, UsageResponse
from mafhum_api.api.v1.auth import _clear_auth_cookies

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
