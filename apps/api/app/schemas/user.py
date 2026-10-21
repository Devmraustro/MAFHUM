"""Current-user profile, quota, export, and account-deletion schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import BaseResponse
from app.schemas.project import ProjectOut


class MeOut(BaseResponse):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None = None
    preferred_locale: str = "ar"
    is_active: bool
    project_quota: int
    projects_count: int
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseResponse):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=200)
    preferred_locale: str | None = Field(default=None)

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Invalid characters in full_name")
        cleaned = value.strip()
        return cleaned or None

    @field_validator("preferred_locale")
    @classmethod
    def _validate_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("ar", "fr"):
            raise ValueError("preferred_locale must be 'ar' or 'fr'")
        return value


class UsageOut(BaseResponse):
    project_quota: int
    projects_used: int
    projects_remaining: int


class AccountExportOut(BaseResponse):
    exported_at: datetime
    user: MeOut
    projects: list[ProjectOut]
    retention_notice: str


class DeleteAccountResponse(BaseResponse):
    detail: str
    deleted_user_id: UUID
    sessions_revoked: int
    projects_deleted: int
    retention_notice: str
