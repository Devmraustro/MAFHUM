"""MAFHUM FastAPI application.

Endpoints:
  GET /health/live                    — liveness
  GET /health/ready                   — readiness (database + AI provider, degraded-safe)
  GET /api/v1/health/*                — versioned aliases of the same handlers
  POST /api/v1/auth/register          — account creation
  POST /api/v1/auth/login             — session login
  POST /api/v1/auth/logout            — revoke current session
  POST /api/v1/auth/logout-all        — revoke all sessions for current user
  GET  /api/v1/auth/sessions          — list active sessions for current user
  DELETE /api/v1/auth/sessions/{id}   — revoke a specific session
  GET  /api/v1/auth/csrf-token        — set session-bound CSRF cookie
  GET/PATCH/DELETE /api/v1/me         — profile, usage, export, account deletion
  GET/POST/PATCH/DELETE /api/v1/projects — user-owned project CRUD
  POST /api/v1/ai/generate            — deterministic mock draft (development/authenticated)
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import current_user, require_csrf, require_same_origin
from app.api.v1.health import health_live, health_ready
from app.api.v1.router import router as v1_router
from app.config import settings
from app.middleware.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.middleware.request_id import RequestIDMiddleware
from app.models.user import User
from app.providers.factory import get_ai_provider
from app.schemas.ai import GenerateRequest, GenerateResponse
from app.schemas.error import SafeErrorEnvelope
from app.schemas.health import HealthResponse


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate production security posture at application startup."""
    settings.validate_for_production()
    yield


app = FastAPI(
    title="MAFHUM",
    description="Academic platform API for Algerian students — modular monolith.",
    version="0.2.0",
    contact={"name": "MAFHUM", "url": "https://github.com/Devmraustro/MAFHUM"},
    license_info={
        "name": "Proprietary — see repository",
        "url": "https://github.com/Devmraustro/MAFHUM",
    },
    lifespan=lifespan,
)

allowed_origins = settings.parsed_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.add_middleware(RequestIDMiddleware)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_exception_handler(
    StarletteHTTPException, http_exception_handler  # type: ignore[arg-type]
)
app.add_exception_handler(
    RequestValidationError, validation_exception_handler  # type: ignore[arg-type]
)

app.include_router(v1_router, prefix=settings.api_prefix)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
async def live(request: Request) -> HealthResponse:
    return health_live(request_id=_request_id(request))


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
async def ready(request: Request) -> HealthResponse:
    return await health_ready(request_id=_request_id(request))


@app.post("/api/v1/ai/generate", response_model=GenerateResponse, tags=["ai"])
async def ai_generate(
    request: Request,
    payload: GenerateRequest,
    user: User | None = Depends(current_user),
) -> GenerateResponse:
    """Deterministic mock draft endpoint.

    Permitted unauthenticated only in local development contract checks; requires
    an authenticated user + valid session-bound CSRF token in staging/production
    and whenever a cookie-authenticated session is present.
    """
    require_same_origin(request)
    if settings.app_env != "development" and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    if user is not None or settings.app_env != "development":
        require_csrf(request)
    provider = get_ai_provider()
    return await provider.generate(payload, request_id=_request_id(request))


@app.get("/contract", response_model=dict[str, Any], tags=["contract"])
def contract() -> dict[str, Any]:
    """Machine-readable contract summary: prefix, health shape, error envelope shape."""
    return {
        "api_prefix": settings.api_prefix,
        "health_live": "GET /health/live",
        "health_ready": "GET /health/ready",
        "openapi": "/openapi.json",
        "error_envelope": SafeErrorEnvelope.model_json_schema(),
    }
