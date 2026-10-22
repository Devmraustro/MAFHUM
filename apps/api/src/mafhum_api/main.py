"""Application factory.

Responsibilities: build the app context, install middleware (request id, security
headers) and map every exception onto the single safe error envelope. Unexpected
exceptions are logged with detail but returned as a generic message — tracebacks,
SQL text, storage paths and parser output never leave the process.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from mafhum_api import __version__
from mafhum_api.api.deps import AppContext, document_limits, worker_config
from mafhum_api.api.v1.health import router as health_router
from mafhum_api.api.v1.router import api_router
from mafhum_api.config import Settings, get_settings
from mafhum_api.core.errors import AuthenticationError, DomainError, ValidationError
from mafhum_api.core.logging_setup import configure_logging
from mafhum_api.core.request_context import REQUEST_ID_HEADER, RequestIdMiddleware, get_request_id
from mafhum_api.db.base import Database
from mafhum_api.services.deletion.service import DeletionService
from mafhum_api.services.documents.service import DocumentService
from mafhum_api.services.documents.storage import LocalFileStorage, StorageError
from mafhum_api.services.documents.worker import ProcessingWorker
from mafhum_api.services.identity.service import IdentityService
from mafhum_api.services.projects.service import ProjectService
from mafhum_api.services.research.retrieval import HttpSourceFetcher
from mafhum_api.services.research.service import ResearchService
from mafhum_api.services.sources.service import SourceService
from mafhum_api.services.study.service import StudyService

logger = logging.getLogger(__name__)

_STATUS_BY_DOMAIN: dict[type[DomainError], int] = {}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response hardening for an API that also serves downloads."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def build_context(settings: Settings | None = None) -> AppContext:
    """Construct every service the API depends on."""
    settings = settings or get_settings()
    database = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    storage = LocalFileStorage(
        settings.storage_root,
        dir_mode=settings.storage_dir_mode,
        file_mode=settings.storage_file_mode,
    )
    limits = document_limits(settings)
    worker = ProcessingWorker(database, storage, limits, worker_config(settings))
    return AppContext(
        settings=settings,
        database=database,
        storage=storage,
        identity=IdentityService(session_ttl_minutes=settings.session_ttl_minutes),
        projects=ProjectService(),
        documents=DocumentService(storage, limits, row_locking=database.supports_row_locking),
        sources=SourceService(),
        deletion=DeletionService(storage),
        research=ResearchService(
            HttpSourceFetcher() if settings.research_retrieval_enabled else None
        ),
        study=StudyService(),
        worker=worker,
    )


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_format == "json")
    settings.assert_production_ready()

    context = build_context(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            context.worker.start()
        logger.info("MAFHUM API started", extra={"event": "app_started"})
        try:
            yield
        finally:
            context.worker.stop()
            context.database.dispose()
            logger.info("MAFHUM API stopped", extra={"event": "app_stopped"})

    app = FastAPI(
        title="MAFHUM API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        # Disabling `docs_url` alone is not enough: FastAPI still serves the
        # generated schema at `/openapi.json`, which hands an unauthenticated
        # caller the complete route, parameter and schema surface. Turn the
        # schema itself off in production.
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
    )
    app.state.context = context

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        envelope = exc.to_envelope(get_request_id())
        payload = envelope.model_dump(exclude_none=True)
        details = payload.get("details")
        if details:
            from mafhum_api.api.deps import safe_details

            filtered = safe_details(details)
            if filtered:
                payload["details"] = filtered
            else:
                payload.pop("details", None)
        status_code = int(exc.status_code)
        headers = {"WWW-Authenticate": "Cookie"} if isinstance(exc, AuthenticationError) else None
        return JSONResponse(status_code=status_code, content=payload, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = error.get("loc", ())
            name = str(location[-1]) if location else "body"
            # ``msg`` comes from Pydantic, not from the caller's data, so it is
            # safe; ``input`` (which can contain the payload) is never included.
            field_errors.setdefault(name, []).append(str(error.get("msg", "invalid")))
        error = ValidationError("البيانات المُرسلة غير صالحة.", field_errors=field_errors)
        return JSONResponse(
            status_code=int(error.status_code),
            content=error.to_envelope(get_request_id()).model_dump(exclude_none=True),
        )

    @app.exception_handler(StorageError)
    async def _storage_error(request: Request, exc: StorageError) -> JSONResponse:
        logger.error("storage failure", extra={"event": "storage_error"})
        error = DomainError("تعذّر إتمام عملية التخزين.")
        error.code = "storage_error"
        error.status_code = 500  # type: ignore[assignment]
        return JSONResponse(
            status_code=500,
            content=error.to_envelope(get_request_id()).model_dump(exclude_none=True),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # The exception is logged but never returned: SQL text can contain data.
        logger.exception("database error", extra={"event": "database_error"})
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "حدث خطأ داخلي. حاول لاحقًا.",
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": _http_code_name(exc.status_code),
                "message": str(exc.detail),
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"event": "unhandled_error"})
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "حدث خطأ داخلي. حاول لاحقًا.",
                "request_id": get_request_id(),
            },
        )


def _http_code_name(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "resource_not_found",
        405: "method_not_allowed",
        413: "payload_too_large",
        415: "unsupported_media_type",
        429: "rate_limited",
    }
    return mapping.get(status_code, "http_error")


# Module-level ASGI application so the conventional deployment invocation
# `uvicorn mafhum_api.main:app` works. It is built from settings at import time,
# which means an insecure production configuration fails fast at startup rather
# than on the first request. The processing worker is started by the lifespan,
# not here, so importing this module has no side effects beyond configuration.
app = create_app()

__all__ = ["REQUEST_ID_HEADER", "AppContext", "app", "build_context", "create_app"]
