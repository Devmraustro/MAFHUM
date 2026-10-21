"""MAFHUM FastAPI application.

Endpoints:
  GET /health/live        — liveness
  GET /health/ready       — readiness (database + AI provider, degraded-safe)
  GET /api/v1/health/*    — versioned aliases of the same handlers
  POST /api/v1/ai/generate — deterministic mock draft (development only)
"""

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.health import health_live, health_ready
from app.api.v1.router import router as v1_router
from app.config import settings
from app.middleware.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.middleware.request_id import RequestIDMiddleware
from app.providers.factory import get_ai_provider
from app.schemas.ai import GenerateRequest, GenerateResponse
from app.schemas.error import SafeErrorEnvelope
from app.schemas.health import HealthResponse

app = FastAPI(
    title="MAFHUM",
    description="Academic platform API for Algerian students — MVP skeleton.",
    version="0.1.0",
    contact={"name": "MAFHUM", "url": "https://github.com/Devmraustro/MAFHUM"},
    license_info={"name": "Proprietary — see repository", "url": "https://github.com/Devmraustro/MAFHUM"},
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
async def ai_generate(request: Request, payload: GenerateRequest) -> GenerateResponse:
    """Development-only deterministic mock draft endpoint.

    Intended as a contract smoke test: feature code calls get_ai_provider()
    directly; this HTTP mirror exists so the mock provider can be exercised
    without curl-to-OpenAPI tooling. Remove or gate behind an admin flag in
    Phase 2.
    """
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
