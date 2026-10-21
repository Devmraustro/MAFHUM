"""Versioned API router — /api/v1."""

from fastapi import APIRouter, Request

from app.api.v1 import auth, projects, users
from app.api.v1.health import health_live, health_ready
from app.schemas.health import HealthResponse

router = APIRouter()

router.include_router(auth.router)
router.include_router(projects.router)
router.include_router(users.router)


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
async def live(request: Request) -> HealthResponse:
    return health_live(request_id=getattr(request.state, "request_id", ""))


@router.get("/health/ready", response_model=HealthResponse, tags=["health"])
async def ready(request: Request) -> HealthResponse:
    return await health_ready(request_id=getattr(request.state, "request_id", ""))
