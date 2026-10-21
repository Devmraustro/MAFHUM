"""Project request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import BaseResponse


class ProjectCreate(BaseResponse):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("Title must not be empty")
        return cleaned

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Description contains invalid null bytes")
        return value


class ProjectUpdate(BaseResponse):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or "\x00" in cleaned:
            raise ValueError("Title must not be empty")
        return cleaned

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Description contains invalid null bytes")
        return value


class ProjectOut(BaseResponse):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    title: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseResponse):
    model_config = ConfigDict(from_attributes=True)

    items: list[ProjectOut]
    limit: int
    offset: int
    total: int
