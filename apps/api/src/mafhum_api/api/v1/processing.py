"""The processing tick: a bounded, idempotent driver for serverless.

Two disjoint principals, two endpoints:

* ``POST /api/v1/processing/tick`` — an authenticated user with the normal
  CSRF guard. The frontend fires one after an upload finalizes so the
  document starts processing without any resident worker.
* ``POST /api/v1/processing/tick-scheduler`` — an unattended scheduler with
  a dedicated bearer token (``PROCESSING_TICK_TOKEN``). No cookies, no CSRF:
  the token *is* the authorization, and a request carrying a bearer header
  can never fall back to a session. When the token is unset the endpoint is
  disabled outright — an unconfigured deployment must not grow a backdoor.

Both refuse (409) while the resident worker is enabled: exactly one driver
per deployment, matching the worker lease's single-writer reasoning. Ticks
never return user data — counters only.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from pydantic import BaseModel

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser
from mafhum_api.core.errors import AuthenticationError, ConflictError, NotFoundError

router = APIRouter(prefix="/processing", tags=["processing"])


class TickResponse(BaseModel):
    """Counters only. No document ids, no users, no filenames."""

    recovered_stale_jobs: int
    expired_upload_sessions: int
    processed: int
    succeeded: int
    failed: int
    stopped_reason: str


def _tick(context) -> TickResponse:  # type: ignore[no-untyped-def]
    if context.settings.processing_worker_enabled:
        # One driver per deployment: the resident worker owns processing,
        # and a tick would double-drive stale recovery and accounting.
        raise ConflictError("معالج الخلفية يعمل بالفعل في هذا النشر.")
    if context.processing_ticker.worker_is_running:
        raise ConflictError("معالج الخلفية يعمل بالفعل في هذا النشر.")
    result = context.processing_ticker.tick()
    return TickResponse(
        recovered_stale_jobs=result.recovered_stale_jobs,
        expired_upload_sessions=result.expired_upload_sessions,
        processed=result.processed,
        succeeded=result.succeeded,
        failed=result.failed,
        stopped_reason=result.stopped_reason,
    )


@router.post("/tick", response_model=TickResponse, dependencies=[CsrfGuard])
def tick(_user: CurrentUser, context: Context) -> TickResponse:
    """Drive one bounded processing pass (authenticated users)."""
    return _tick(context)


@router.post("/tick-scheduler", response_model=TickResponse)
def tick_scheduler(request: Request, context: Context) -> TickResponse:
    """Drive one bounded processing pass (unattended scheduler token)."""
    expected = context.settings.processing_tick_token
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not expected:
        # The scheduler endpoint is disabled unless a token is configured.
        # 404, not 401: an unconfigured route must not advertise itself.
        raise NotFoundError()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise AuthenticationError()
    return _tick(context)


__all__ = ["router"]
