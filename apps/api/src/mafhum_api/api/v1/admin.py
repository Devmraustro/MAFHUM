"""Read-only administration: overview, usage, audit trail and metrics.

Every route requires the database-backed ``admin`` role. There is deliberately
**no** write endpoint: roles are granted by an operator CLI against the
database, and no HTTP call can promote a user, edit an event, delete an event or
read another user's documents. A non-admin receives the standard 404 envelope,
and the denial is recorded.

The metrics endpoint emits a small, redacted Prometheus-style text body. It
contains counters only — no email, filename, URL, token or document text — so it
can be scraped by a monitoring system without becoming a data leak.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from mafhum_api.api.deps import (
    AdminUser,
    Context,
    DbSession,
    Page,
    PageSize,
)
from mafhum_api.services.audit.service import event_to_dict

router = APIRouter(prefix="/admin", tags=["admin"])


class OverviewResponse(BaseModel):
    active_users: int
    documents: int
    presentation_jobs: int
    presentation_artifacts: int
    audit_events: int
    denied_events: int
    documents_by_status: dict[str, int]
    presentation_jobs_by_status: dict[str, int]


class UsageItemResponse(BaseModel):
    user_id: str
    email: str
    role: str
    created_at: str | None
    documents: int
    bytes: int
    presentations: int


class UsageListResponse(BaseModel):
    items: list[UsageItemResponse]
    total: int
    page: int
    page_size: int


class AuditEventResponse(BaseModel):
    id: str
    created_at: str | None
    actor_id: str | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    request_id: str | None
    details: dict[str, Any]


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    page: int
    page_size: int


@router.get("/overview", response_model=OverviewResponse)
def overview(user: AdminUser, context: Context, db: DbSession) -> OverviewResponse:
    data = context.audit.overview(db)
    context.audit.record(db, action="admin_overview_viewed", actor_id=user.id, target_type="admin")
    db.commit()
    return OverviewResponse(**data)


@router.get("/usage", response_model=UsageListResponse)
def usage(
    user: AdminUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
) -> UsageListResponse:
    items, total = context.audit.usage(db, page=page, page_size=page_size)
    context.audit.record(
        db,
        action="admin_usage_viewed",
        actor_id=user.id,
        target_type="admin",
        details={"count": len(items)},
    )
    db.commit()
    return UsageListResponse(
        items=[UsageItemResponse(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/audit-events", response_model=AuditEventListResponse)
def audit_events(
    user: AdminUser,
    context: Context,
    db: DbSession,
    page: Page,
    page_size: PageSize,
    action: str | None = None,
    outcome: str | None = None,
) -> AuditEventListResponse:
    events, total = context.audit.list_events(
        db, page=page, page_size=page_size, action=action, outcome=outcome
    )
    context.audit.record(db, action="admin_audit_viewed", actor_id=user.id, target_type="admin")
    db.commit()
    return AuditEventListResponse(
        items=[AuditEventResponse(**event_to_dict(event)) for event in events],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(user: AdminUser, context: Context, db: DbSession) -> str:
    data = context.audit.overview(db)
    lines: list[str] = [
        "# HELP mafhum_active_users Accounts not pending deletion.",
        "# TYPE mafhum_active_users gauge",
        f"mafhum_active_users {data['active_users']}",
        "# HELP mafhum_documents_total Documents by processing status.",
        "# TYPE mafhum_documents_total gauge",
    ]
    for status, count in data["documents_by_status"].items():
        lines.append(f'mafhum_documents_total{{status="{status}"}} {count}')
    lines += [
        "# HELP mafhum_presentation_jobs_total Generated decks by job status.",
        "# TYPE mafhum_presentation_jobs_total gauge",
    ]
    for status, count in data["presentation_jobs_by_status"].items():
        lines.append(f'mafhum_presentation_jobs_total{{status="{status}"}} {count}')
    lines += [
        "# HELP mafhum_audit_denied_total Denied admin/audit events.",
        "# TYPE mafhum_audit_denied_total counter",
        f"mafhum_audit_denied_total {data['denied_events']}",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["router"]
