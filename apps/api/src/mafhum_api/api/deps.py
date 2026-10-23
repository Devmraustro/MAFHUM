"""FastAPI dependency wiring: app context, auth, CSRF and pagination."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response
from sqlalchemy.orm import Session

from mafhum_api.config import Settings
from mafhum_api.core.errors import (
    AuthenticationError,
    DomainError,
    RateLimitedError,
    ValidationError,
)
from mafhum_api.core.request_context import get_request_id
from mafhum_api.core.security import tokens_match, verify_origin
from mafhum_api.db.base import Database
from mafhum_api.db.models import User
from mafhum_api.services.deletion.service import DeletionService
from mafhum_api.services.documents.service import DocumentLimits, DocumentService
from mafhum_api.services.documents.storage import DocumentStorage
from mafhum_api.services.documents.worker import ProcessingWorker, WorkerConfig
from mafhum_api.services.identity.service import AuthenticatedUser, IdentityService
from mafhum_api.services.identity.throttle import LoginThrottle
from mafhum_api.services.projects.service import ProjectService
from mafhum_api.services.research.service import ResearchService
from mafhum_api.services.sources.service import SourceService
from mafhum_api.services.study.service import StudyService


@dataclass(slots=True)
class AppContext:
    """Everything the API needs, constructed once at startup."""

    settings: Settings
    database: Database
    storage: DocumentStorage
    identity: IdentityService
    projects: ProjectService
    documents: DocumentService
    sources: SourceService
    deletion: DeletionService
    research: ResearchService
    study: StudyService
    worker: ProcessingWorker
    login_throttle: LoginThrottle

    @property
    def limits(self) -> DocumentLimits:
        return document_limits(self.settings)

    @property
    def worker_config(self) -> WorkerConfig:
        return worker_config(self.settings)


def document_limits(settings: Settings) -> DocumentLimits:
    """Project the application settings onto the document service's limits."""
    return DocumentLimits(
        max_upload_bytes=settings.max_upload_bytes,
        max_extracted_chars=settings.max_extracted_chars,
        max_pdf_pages=settings.max_pdf_pages,
        max_docx_entries=settings.max_docx_entries,
        max_docx_uncompressed_bytes=settings.max_docx_uncompressed_bytes,
        max_archive_compression_ratio=settings.max_archive_compression_ratio,
        max_documents_per_user=settings.max_documents_per_user,
        max_total_bytes_per_user=settings.max_total_bytes_per_user,
        max_segment_chars=settings.max_segment_chars,
    )


def worker_config(settings: Settings) -> WorkerConfig:
    return WorkerConfig(
        worker_count=settings.processing_worker_count,
        job_timeout_seconds=settings.processing_job_timeout_seconds,
        stale_after_seconds=settings.processing_stale_after_seconds,
        max_attempts=settings.processing_max_attempts,
    )


def get_context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


def get_db(context: Annotated[AppContext, Depends(get_context)]) -> Iterator[Session]:
    session = context.database.session()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
Context = Annotated[AppContext, Depends(get_context)]


# --------------------------------------------------------------------- session
def current_user(
    request: Request,
    context: Context,
    db: DbSession,
) -> User:
    """Resolve the caller from their session cookie, or reject."""
    token = request.cookies.get(context.settings.session_cookie_name)
    return context.identity.resolve_session(db, token)


CurrentUser = Annotated[User, Depends(current_user)]


def authenticated_user(
    user: CurrentUser,
    context: Context,
) -> AuthenticatedUser:
    return context.identity.describe(user)


AuthedUser = Annotated[AuthenticatedUser, Depends(authenticated_user)]


# ------------------------------------------------------------------------ CSRF
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def enforce_csrf(
    request: Request,
    response: Response,
    context: Context,
    _user: CurrentUser,
) -> None:
    """Protect cookie-authenticated state changes.

    Two independent checks:
    1. **Origin/Referer allowlist** — blocks cross-site form posts and simple
       CORS-less requests.
    2. **Double-submit token** — the caller must echo the value of the
       non-HttpOnly CSRF cookie in ``X-CSRF-Token``. An attacker's cross-origin
       script can trigger the request but cannot read the cookie to fill the
       header.

    Multipart uploads carry the same token in a form field, because setting
    custom headers is inconvenient for plain ``<form>`` submissions.
    """
    if request.method not in _MUTATING_METHODS:
        return
    settings = context.settings
    verdict = verify_origin(
        request.headers.get("origin"),
        request.headers.get("referer"),
        settings.allowed_origins,
    )
    if not verdict.allowed:
        raise _csrf_error("origin_rejected")

    expected = request.cookies.get(settings.csrf_cookie_name)
    provided = request.headers.get(settings.csrf_header_name)
    if not expected or not provided or not tokens_match(provided, expected):
        raise _csrf_error("csrf_token_mismatch")


def _csrf_error(reason: str) -> DomainError:
    error = ValidationError(
        "تعذّر التحقق من أمان الطلب. أعد تحميل الصفحة وحاول مجددًا.",
        field_errors={"csrf": [reason]},
    )
    error.code = "csrf_failed"
    return error


# For use in ``dependencies=[CsrfGuard]`` on a route decorator.
CsrfGuard = Depends(enforce_csrf)
# For use as a function parameter annotation when a handler wants it injected.
CsrfGuardAnnotated = Annotated[None, Depends(enforce_csrf)]


# ------------------------------------------------------------------ pagination
# The parameter names here ARE the public query-string keys: FastAPI derives the
# query alias from the dependency's parameter name, so a generic name such as
# ``value`` would silently bind ``?value=`` and leave ``?page_size=`` ignored.
def page_query(page: Annotated[int, Query(ge=1, le=10_000)] = 1) -> int:
    return page


def page_size_query(page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> int:
    return page_size


Page = Annotated[int, Depends(page_query)]
PageSize = Annotated[int, Depends(page_size_query)]


# -------------------------------------------------------------- misc utilities
def parse_uuid(raw: str, field: str = "id") -> uuid.UUID:
    """Parse an identifier, mapping failure onto the standard envelope."""
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError("المعرّف غير صالح.", field_errors={field: ["معرّف غير صالح."]}) from exc


def request_id_of() -> str | None:
    return get_request_id()


def safe_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pass through only allow-listed, non-sensitive detail keys."""
    if not details:
        return None
    allowed = {
        "reason",
        "allowed",
        "expected_format",
        "declared",
        "detected",
        "hint",
        "max_bytes",
        "max_pages",
        "max_entries",
        "field",
    }
    filtered = {key: value for key, value in details.items() if key in allowed}
    return filtered or None


__all__ = [
    "AppContext",
    "AuthedUser",
    "AuthenticationError",
    "Context",
    "CsrfGuard",
    "CsrfGuardAnnotated",
    "CurrentUser",
    "DbSession",
    "Page",
    "PageSize",
    "RateLimitedError",
    "document_limits",
    "get_context",
    "get_db",
    "parse_uuid",
    "safe_details",
    "worker_config",
]
