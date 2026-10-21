"""Authentication & session request/response schemas."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import BaseResponse

EMAIL_RE = re.compile(r"^[^@\s\x00-\x1f]+@[^@\s\x00-\x1f]+\.[^@\s\x00-\x1f]{2,}\Z")


class UserRegister(BaseResponse):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    preferred_locale: str = Field(default="ar")

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_RE.match(normalized):
            raise ValueError("Invalid email address")
        return normalized

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if "\x00" in value or not value.strip():
            raise ValueError("Password must not be blank or contain null bytes")
        return value

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("preferred_locale")
    @classmethod
    def _validate_locale(cls, value: str) -> str:
        if value not in ("ar", "fr"):
            raise ValueError("preferred_locale must be 'ar' or 'fr'")
        return value


class UserLogin(BaseResponse):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_RE.match(normalized):
            raise ValueError("Invalid email address")
        return normalized


class UserOut(BaseResponse):
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


class SessionOut(BaseResponse):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    is_current: bool = False


class SessionListOut(BaseResponse):
    items: list[SessionOut]


class AuthMessage(BaseResponse):
    detail: str
