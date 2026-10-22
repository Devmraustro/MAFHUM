"""Safe, consistent error envelope and domain exceptions.

No exception message reaching a client may contain: filesystem paths, SQL text,
stack traces, or document content. Callers raise typed domain errors; the API
layer maps them onto the envelope defined in ``docs/PROJECT_ARCHITECTURE.md``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel


class ErrorEnvelope(BaseModel):
    """The single error response shape used across ``/api/v1``."""

    code: str
    message: str
    request_id: str | None = None
    field_errors: dict[str, list[str]] | None = None
    details: dict[str, Any] | None = None


class DomainError(Exception):
    """Base class for expected, client-safe failures."""

    status_code: HTTPStatus = HTTPStatus.BAD_REQUEST
    code: str = "error"
    default_message: str = "Request could not be completed."

    def __init__(
        self, message: str | None = None, *, details: dict[str, Any] | None = None
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    @property
    def field_errors(self) -> dict[str, list[str]] | None:
        """Per-field messages. Only ``ValidationError`` populates this."""
        return None

    def to_envelope(self, request_id: str | None) -> ErrorEnvelope:
        return ErrorEnvelope(
            code=self.code,
            message=self.message,
            request_id=request_id,
            field_errors=self.field_errors,
            details=self.details,
        )


class ValidationError(DomainError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"
    default_message = "البيانات المُرسلة غير صالحة."

    def __init__(
        self,
        message: str | None = None,
        *,
        field_errors: dict[str, list[str]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self._field_errors = field_errors

    @property
    def field_errors(self) -> dict[str, list[str]] | None:
        return self._field_errors


class AuthenticationError(DomainError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "authentication_required"
    default_message = "يجب تسجيل الدخول للمتابعة."


class AuthorizationError(DomainError):
    """Raised when a user touches a resource they do not own.

    The message is deliberately identical to the not-found message so that
    probing an identifier cannot reveal whether a resource exists.
    """

    status_code = HTTPStatus.NOT_FOUND
    code = "resource_not_found"
    default_message = "المورد غير موجود."


class NotFoundError(DomainError):
    status_code = HTTPStatus.NOT_FOUND
    code = "resource_not_found"
    default_message = "المورد غير موجود."


class ConflictError(DomainError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"
    default_message = "الطلب يتعارض مع الحالة الحالية."


class QuotaExceededError(DomainError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "quota_exceeded"
    default_message = "تجاوزت الحد المسموح به."


class RateLimitedError(DomainError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate_limited"
    default_message = "طلبات كثيرة جدًا، حاول لاحقًا."


class FileRejectedError(DomainError):
    """Upload failed server-side validation. ``details.code`` is machine-readable."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "file_rejected"
    default_message = "الملف مرفوض."


class StorageError(DomainError):
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "storage_error"
    default_message = "تعذّر إتمام عملية التخزين."


class ProcessingUnavailableError(DomainError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "processing_unavailable"
    default_message = "خدمة المعالجة غير متاحة حاليًا."
