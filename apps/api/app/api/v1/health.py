"""Versioned health endpoints under /api/v1."""

from sqlalchemy import text

from app.api.deps import get_engine
from app.config import settings
from app.providers.factory import get_ai_provider
from app.schemas.health import CheckItem, HealthResponse


def _database_check() -> CheckItem:
    engine = get_engine()
    if engine is None:
        return CheckItem(status="unavailable", detail="database_not_configured")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return CheckItem(status="ok")
    except Exception as exc:  # noqa: BLE001 - degraded, not fatal
        return CheckItem(status="unavailable", detail=type(exc).__name__)


async def _ai_provider_check() -> CheckItem:
    try:
        health = await get_ai_provider().health()
        return CheckItem(
            status="ok" if health.status == "ok" else "degraded",
            detail=f"{health.provider} ({health.model})",
        )
    except Exception as exc:  # noqa: BLE001 - degraded, not fatal
        return CheckItem(status="unavailable", detail=type(exc).__name__)


def health_live(request_id: str) -> HealthResponse:
    """Liveness: the process is up. No dependency checks."""
    return HealthResponse(
        status="ok",
        version=settings.app_name and "0.1.0",
        checks={"live": CheckItem(status="ok")},
        request_id=request_id,
    )


async def health_ready(request_id: str) -> HealthResponse:
    """Readiness: database and AI provider checks; degrades safely."""
    import anyio

    db = await anyio.to_thread.run_sync(_database_check)
    ai = await _ai_provider_check()
    checks = {"database": db, "ai_provider": ai}
    overall = "ok" if all(c.status == "ok" for c in checks.values()) else "degraded"
    return HealthResponse(status=overall, version="0.1.0", checks=checks, request_id=request_id)
