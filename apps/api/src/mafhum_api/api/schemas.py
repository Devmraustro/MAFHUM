"""Pydantic request/response schemas for ``/api/v1``.

Arabic is the primary interface language, so validation messages are written in
Arabic. Field names stay in English as the stable API contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ------------------------------------------------------------------------ auth
class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(description="البريد الإلكتروني")
    password: str = Field(min_length=8, max_length=256, description="كلمة المرور")
    display_name: str = Field(default="", max_length=120, description="الاسم الظاهر")

    @field_validator("password")
    @classmethod
    def _no_whitespace_only(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("كلمة المرور غير صالحة.")
        return v


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    preferred_language: str


class SessionResponse(BaseModel):
    user: UserResponse
    expires_at: datetime


class CsrfTokenResponse(BaseModel):
    csrf_token: str


# -------------------------------------------------------------------- projects
class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200, description="عنوان المشروع")
    description: str = Field(default="", max_length=4000)
    language: Literal["ar", "fr", "en"] = "ar"


class ProjectResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    language: str
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int


# ------------------------------------------------------------------- documents
class DocumentResponse(BaseModel):
    """Document metadata. Never includes a storage path or absolute file path."""

    id: uuid.UUID
    display_filename: str
    format: Literal["pdf", "docx"]
    size_bytes: int
    status: Literal["pending", "processing", "ready", "failed"]
    page_count: int | None = None
    char_count: int
    segment_count: int
    project_id: uuid.UUID | None = None
    language_hint: str | None = None
    is_scanned: bool | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class DocumentTextSegmentResponse(BaseModel):
    ordinal: int
    page_number: int | None
    text: str
    char_count: int


class DocumentTextResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    items: list[DocumentTextSegmentResponse]
    total: int
    page: int
    page_size: int


class DocumentRetryResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class DocumentDeletedResponse(BaseModel):
    id: uuid.UUID
    deleted: bool


class UsageResponse(BaseModel):
    documents: int
    total_bytes: int
    max_documents: int
    max_total_bytes: int


class LimitsResponse(BaseModel):
    """Server-authoritative upload limits, so the UI cannot drift from them."""

    max_upload_mb: int
    max_pdf_pages: int
    max_documents_per_user: int
    max_total_bytes_per_user_mb: int
    allowed_extensions: list[str]
    ocr_available: bool = False
    # Disclosed so no client can assume an upload was virus-checked. MAFHUM
    # ships no malware engine; see services/documents/scanning.py.
    malware_scanning_available: bool = False


# --------------------------------------------------------------------- sources
class SourceCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=500)
    authors: list[str] | None = Field(default=None, max_length=64)
    institution: str | None = Field(default=None, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    publication_year: int | None = Field(default=None, ge=1000, le=2100)
    source_type: Literal[
        "user_upload",
        "lecture_notes",
        "book",
        "journal_article",
        "thesis",
        "report",
        "legal_text",
        "website",
        "other",
    ] = "user_upload"
    doi: str | None = Field(default=None, max_length=255)
    isbn: str | None = Field(default=None, max_length=32)
    url: str | None = Field(default=None, max_length=1024)
    repository_identifier: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=8)
    page_count: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=4000)
    document_id: uuid.UUID | None = None

    @field_validator("authors")
    @classmethod
    def _clean_authors(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [a.strip()[:255] for a in v if isinstance(a, str) and a.strip()]


class SourceResponse(BaseModel):
    id: uuid.UUID
    title: str | None = None
    authors: list[str] | None = None
    institution: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    source_type: str
    doi: str | None = None
    isbn: str | None = None
    url: str | None = None
    repository_identifier: str | None = None
    language: str | None = None
    page_count: int | None = None
    provenance: str
    verification_status: str
    verified_by: str | None = None
    verified_at: datetime | None = None
    document_id: uuid.UUID | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


class SourceListResponse(BaseModel):
    items: list[SourceResponse]
    total: int
    page: int
    page_size: int


# ------------------------------------------------------------------ account
class AccountDeletionResponse(BaseModel):
    deleted: bool
    complete: bool
    documents_removed: int
    blobs_removed: int
    blobs_pending: int
    detail: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, str] = Field(default_factory=dict)
