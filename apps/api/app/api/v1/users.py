"""Current-user routes: profile, usage quotas, export, and account deletion."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session as SaSession

from app.api.deps import require_csrf, require_db, require_user, set_auth_cookies
from app.models.user import User
from app.schemas.project import ProjectOut
from app.schemas.user import (
    AccountExportOut,
    DeleteAccountResponse,
    MeOut,
    ProfileUpdate,
    UsageOut,
)
from app.services.auth import RETENTION_NOTICE, AuthService
from app.services.project import ProjectService

router = APIRouter(tags=["users"])


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(require_user)) -> MeOut:
    return MeOut.model_validate(user)


@router.patch("/me", response_model=MeOut)
def update_me(
    request: Request,
    payload: ProfileUpdate,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> MeOut:
    require_csrf(request)
    service = AuthService(db)
    updated = service.update_profile(
        user,
        full_name=payload.full_name,
        update_full_name=("full_name" in payload.model_fields_set),
        preferred_locale=payload.preferred_locale,
        update_locale=("preferred_locale" in payload.model_fields_set),
    )
    db.commit()
    db.refresh(updated)
    return MeOut.model_validate(updated)


@router.get("/me/usage", response_model=UsageOut)
def get_usage(
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> UsageOut:
    used = ProjectService(db, user).count_projects()
    remaining = max(user.project_quota - used, 0)
    return UsageOut(
        project_quota=user.project_quota,
        projects_used=used,
        projects_remaining=remaining,
    )


@router.get("/me/export", response_model=AccountExportOut)
def export_account(
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> AccountExportOut:
    projects = ProjectService(db, user).list_all_owned_projects()
    return AccountExportOut(
        exported_at=datetime.now(UTC),
        user=MeOut.model_validate(user),
        projects=[ProjectOut.model_validate(p) for p in projects],
        retention_notice=RETENTION_NOTICE,
    )


@router.delete("/me", response_model=DeleteAccountResponse)
def delete_account(
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> DeleteAccountResponse:
    require_csrf(request)
    deleted_id = UUID(user.id)
    service = AuthService(db)
    sessions_revoked, projects_deleted = service.delete_account(user.id)
    db.commit()
    set_auth_cookies(response, None, None)
    return DeleteAccountResponse(
        detail="Account deleted",
        deleted_user_id=deleted_id,
        sessions_revoked=sessions_revoked,
        projects_deleted=projects_deleted,
        retention_notice=RETENTION_NOTICE,
    )
