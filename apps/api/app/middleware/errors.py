"""Safe error mapping: consistent envelope, no internals leaked."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.schemas.error import ErrorField, SafeErrorEnvelope


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=SafeErrorEnvelope(
            code=f"http_{exc.status_code}",
            message=str(exc.detail) if isinstance(exc.detail, str) else "Request error",
            request_id=request_id(request),
        ).model_dump(),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    field_errors = [
        ErrorField(
            field=".".join(str(loc) for loc in error.get("loc", [])[1:]),
            message=error.get("msg", "Invalid value"),
            code=error.get("type"),
        )
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=SafeErrorEnvelope(
            code="validation_error",
            message="Invalid request payload.",
            request_id=request_id(request),
            field_errors=field_errors,
        ).model_dump(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never return stack traces or internals to the client.
    return JSONResponse(
        status_code=500,
        content=SafeErrorEnvelope(
            code="internal_server_error",
            message="An unexpected error occurred.",
            request_id=request_id(request),
        ).model_dump(),
    )
