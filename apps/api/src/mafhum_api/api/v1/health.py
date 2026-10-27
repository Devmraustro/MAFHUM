"""Liveness and readiness probes. Neither may leak secrets or internals."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from mafhum_api import __version__
from mafhum_api.api.deps import Context, DbSession, get_context
from mafhum_api.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse, include_in_schema=True)
def live() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/health/ready", response_model=HealthResponse)
def ready(context: Context, db: DbSession) -> HealthResponse:
    """Report dependency health without exposing connection details."""
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    checks["storage"] = "ok" if _storage_writable(context) else "unavailable"
    checks["processing_worker"] = "ok" if context.worker.worker_id else "stopped"
    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return HealthResponse(status=status, version=__version__, checks=checks)  # type: ignore[arg-type]


def _storage_writable(context: object) -> bool:
    storage = getattr(context, "storage", None)
    root = getattr(storage, "root", None)
    if root is None:
        return False
    try:
        from pathlib import Path

        return Path(root).is_dir()
    except OSError:
        return False


__all__ = ["get_context", "router"]
