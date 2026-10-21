"""Request ID middleware: every response carries a validated X-Request-ID."""

import re
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"
SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\Z")


def sanitize_request_id(candidate: str | None) -> str:
    if candidate and SAFE_REQUEST_ID_RE.match(candidate):
        return candidate
    return str(uuid.uuid4())


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
