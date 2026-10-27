"""Request-id propagation.

A request ID is generated server-side, returned as ``X-Request-Id`` and attached
to every log record. It must never contain request payload material.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"

_request_id: ContextVar[str | None] = ContextVar("mafhum_request_id", default=None)


def new_request_id() -> str:
    """Return an opaque, URL-safe, non-guessable request identifier."""
    return secrets.token_urlsafe(12)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def _sanitize_incoming(value: str) -> str:
    """Clamp a client-supplied request id to a safe, bounded token."""
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")
    return cleaned[:64] or new_request_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign or validate a request id and expose it on the response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = _sanitize_incoming(incoming) if incoming else new_request_id()
        token = _request_id.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def log_context() -> dict[str, Any]:
    """Extra fields to attach to structured log records."""
    return {"request_id": _request_id.get()}
