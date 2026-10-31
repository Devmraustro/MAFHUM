"""Append-only audit events and read-only administrative aggregation.

Privacy rules for this module are strict, because an audit log is the one place
where a developer is tempted to dump everything:

* ``details`` accepts only an allow-list of short, non-identifying keys. No
  email, document title, filename, URL, IP address, password, token or extracted
  text may be stored there.
* ``actor_id`` is nullable with ``ON DELETE SET NULL``: account deletion keeps
  the event but drops the dangling reference.
* No endpoint in this package mutates roles, deletes events or edits an event.
  Role grants happen through the operator CLI (``apps/api/tools/set_admin.py``),
  which writes its own audit row.

The admin API exposes aggregates and event metadata only. It does not expose a
generic query language, so an admin cannot use it to read another user's
documents.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mafhum_api.core.request_context import get_request_id
from mafhum_api.db.base import Database, execute_rowcount
from mafhum_api.db.models import (
    AuditEvent,
    AuditOutcome,
    DeletionState,
    Document,
    PresentationArtifact,
    PresentationJob,
    User,
)

logger = logging.getLogger(__name__)

ACTIONS: frozenset[str] = frozenset(
    {
        "auth_register",
        "auth_login",
        "auth_login_failed",
        "auth_logout",
        "account_deleted",
        "account_exported",
        "audit_events_purged",
        "document_uploaded",
        "document_deleted",
        "presentation_created",
        "presentation_deleted",
        "admin_overview_viewed",
        "admin_usage_viewed",
        "admin_audit_viewed",
        "admin_metrics_viewed",
        "admin_access_denied",
        "admin_role_changed",
    }
)

_ALLOWED_DETAIL_KEYS: frozenset[str] = frozenset({"reason", "resource", "status", "code", "count"})
_MAX_DETAIL_CHARS = 120
_MAX_PAGE_SIZE = 100
DENIAL_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class PruneResult:
    """Outcome of one bounded retention run. Never a destructive surprise."""

    deleted: int
    batches: int
    truncated: bool
    cutoff: datetime | None
    retention_days: int
    reason: str
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "batches": self.batches,
            "truncated": self.truncated,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "retention_days": self.retention_days,
            "reason": self.reason,
            "dry_run": self.dry_run,
        }


def _safe_details(details: dict[str, object] | None) -> dict[str, object]:
    if not details:
        return {}
    cleaned: dict[str, object] = {}
    for key, value in details.items():
        if key not in _ALLOWED_DETAIL_KEYS:
            continue
        if isinstance(value, (bool, int)):
            cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:_MAX_DETAIL_CHARS]
    return cleaned


class AuditService:
    def record(
        self,
        db: Session,
        *,
        action: str,
        actor_id: uuid.UUID | None = None,
        outcome: str = AuditOutcome.SUCCESS.value,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict[str, object] | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        """Append one event. The caller owns the transaction boundary."""
        if action not in ACTIONS:
            # A new action must be added to the allow-list on purpose. Fail
            # loudly in development instead of writing an unclassified row.
            raise ValueError("unregistered audit action")
        if outcome not in {item.value for item in AuditOutcome}:
            raise ValueError("invalid audit outcome")
        event = AuditEvent(
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            target_type=target_type[:32] if target_type else None,
            target_id=target_id[:64] if target_id else None,
            request_id=(request_id or get_request_id() or None),
            details=_safe_details(details),
        )
        if event.request_id:
            event.request_id = event.request_id[:64]
        db.add(event)
        db.flush()
        return event

    def record_denied_once(
        self,
        database: Database,
        *,
        actor_id: uuid.UUID,
        window_seconds: int = DENIAL_WINDOW_SECONDS,
        **kwargs: Any,
    ) -> bool:
        """Record a denial at most once per actor per window, in the database.

        A non-admin can otherwise flood the audit table by repeatedly probing
        admin URLs. The check is a COUNT over an indexed column on a fresh
        session, so it holds across replicas; two simultaneous first requests
        may still produce two rows, which is an acceptable bound. Returns True
        when a row was written.
        """
        try:
            with database.session() as session:
                cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
                recent = (
                    session.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(
                            AuditEvent.actor_id == actor_id,
                            AuditEvent.action == "admin_access_denied",
                            AuditEvent.created_at >= cutoff,
                        )
                    )
                    or 0
                )
                if recent:
                    return False
                self.record(
                    session,
                    action="admin_access_denied",
                    actor_id=actor_id,
                    outcome="denied",
                    **kwargs,
                )
                session.commit()
                return True
        except Exception:
            logger.error("audit denial record failed", extra={"event": "audit_write_failed"})
            return False

    def record_detached(self, database: Database, **kwargs: Any) -> None:
        """Record on a fresh session, for paths whose request session is doomed.

        A failed login raises before the request session can commit; recording on
        that session would be rolled back. This uses its own transaction so the
        denial is still visible to an admin.
        """
        try:
            with database.session() as session:
                self.record(session, **kwargs)
                session.commit()
        except Exception:  # audit must never break the request
            logger.error("audit record failed", extra={"event": "audit_write_failed"})

    # -------------------------------------------------------------- retention
    def prune(
        self,
        database: Database,
        *,
        retention_days: int,
        batch_size: int = 1000,
        max_batches: int = 10,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> PruneResult:
        """Delete old audit rows in bounded, independently committed batches.

        Deliberately NOT automatic. Retention is a legal/business decision, so
        nothing in request handling or startup calls this. An operator runs the
        CLI (default: dry run) after confirming the policy and any legal hold.

        Safety properties:
        * ``retention_days == 0`` is a no-op — the historical "keep forever"
          behaviour is preserved until a policy is explicitly configured.
        * Only ``audit_events`` rows older than the cutoff are touched. There is
          no cascade, no TRUNCATE and no foreign key to a dependent table.
        * Each batch is its own short transaction, capped at ``batch_size``;
          ``max_batches`` caps the whole run. A large backlog therefore needs
          several scheduled runs rather than one long lock.
        * A dry run answers "how many would this batch remove" without deleting.
        * The purge itself is audited, so an operator cannot silently erase the
          trail. That row is written after the cutoff and survives the next run.
        """
        if retention_days < 0:
            raise ValueError("retention_days must be >= 0")
        if batch_size < 1 or max_batches < 1:
            raise ValueError("batch_size and max_batches must be >= 1")
        if retention_days == 0:
            return PruneResult(
                deleted=0,
                batches=0,
                truncated=False,
                cutoff=None,
                retention_days=0,
                reason="retention_disabled",
                dry_run=dry_run,
            )

        cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
        deleted = 0
        batches = 0
        for _ in range(max_batches):
            with database.unit_of_work() as session:
                candidates = (
                    select(AuditEvent.id)
                    .where(AuditEvent.created_at < cutoff)
                    .order_by(AuditEvent.created_at)
                    .limit(batch_size)
                )
                if dry_run:
                    removed = int(
                        session.scalar(
                            select(func.count())
                            .select_from(AuditEvent)
                            .where(AuditEvent.created_at < cutoff)
                        )
                        or 0
                    )
                    removed = min(removed, batch_size)
                else:
                    removed = execute_rowcount(
                        session, delete(AuditEvent).where(AuditEvent.id.in_(candidates))
                    )
            batches += 1
            deleted += removed
            if dry_run or removed < batch_size:
                break

        with database.session() as session:
            remaining = int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.created_at < cutoff)
                )
                or 0
            )
        truncated = remaining > 0

        if not dry_run:
            with database.unit_of_work() as session:
                self.record(
                    session,
                    action="audit_events_purged",
                    target_type="audit_event",
                    details={"count": deleted, "status": "truncated" if truncated else "ok"},
                )

        return PruneResult(
            deleted=deleted,
            batches=batches,
            truncated=truncated,
            cutoff=cutoff,
            retention_days=retention_days,
            reason="ok" if not truncated else "batch_limit_reached",
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------ reads
    def list_events(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        action: str | None = None,
        outcome: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        def filtered(statement: Any) -> Any:
            if action:
                statement = statement.where(AuditEvent.action == action)
            if outcome:
                statement = statement.where(AuditEvent.outcome == outcome)
            return statement

        total = db.scalar(filtered(select(func.count()).select_from(AuditEvent))) or 0
        rows = list(
            db.scalars(
                filtered(
                    select(AuditEvent)
                    .order_by(AuditEvent.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return rows, total

    def overview(self, db: Session) -> dict[str, Any]:
        """Small, bounded set of counters. No row-level user data here."""
        active_users = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.deletion_state == DeletionState.ACTIVE.value)
            )
            or 0
        )
        documents = db.scalar(select(func.count()).select_from(Document)) or 0
        presentations = db.scalar(select(func.count()).select_from(PresentationJob)) or 0
        artifacts = db.scalar(select(func.count()).select_from(PresentationArtifact)) or 0
        events = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
        denied = (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.outcome == AuditOutcome.DENIED.value)
            )
            or 0
        )
        documents_by_status: dict[str, int] = {}
        for status, count in db.execute(
            select(Document.status, func.count()).group_by(Document.status)
        ).all():
            documents_by_status[str(status)] = int(count)
        jobs_by_status: dict[str, int] = {}
        for status, count in db.execute(
            select(PresentationJob.status, func.count()).group_by(PresentationJob.status)
        ).all():
            jobs_by_status[str(status)] = int(count)
        return {
            "active_users": int(active_users),
            "documents": int(documents),
            "presentation_jobs": int(presentations),
            "presentation_artifacts": int(artifacts),
            "audit_events": int(events),
            "denied_events": int(denied),
            "documents_by_status": documents_by_status,
            "presentation_jobs_by_status": jobs_by_status,
        }

    def usage(self, db: Session, *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        """Per-account aggregate usage for capacity planning.

        The email is returned because an operator must be able to identify an
        account to act on it; it is available only to an authenticated admin and
        is never written to logs or audit details.
        """
        document_count = (
            select(func.count())
            .select_from(Document)
            .where(Document.owner_id == User.id)
            .correlate(User)
            .scalar_subquery()
        )
        document_bytes = (
            select(func.coalesce(func.sum(Document.size_bytes), 0))
            .select_from(Document)
            .where(Document.owner_id == User.id)
            .correlate(User)
            .scalar_subquery()
        )
        presentation_count = (
            select(func.count())
            .select_from(PresentationJob)
            .where(PresentationJob.owner_id == User.id)
            .correlate(User)
            .scalar_subquery()
        )
        total = db.scalar(select(func.count()).select_from(User)) or 0
        rows = db.execute(
            select(
                User.id,
                User.email,
                User.role,
                User.created_at,
                document_count.label("documents"),
                document_bytes.label("bytes"),
                presentation_count.label("presentations"),
            )
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(min(page_size, _MAX_PAGE_SIZE))
        ).all()
        items = [
            {
                "user_id": str(row.id),
                "email": row.email,
                "role": row.role,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "documents": int(row.documents),
                "bytes": int(row.bytes),
                "presentations": int(row.presentations),
            }
            for row in rows
        ]
        return items, int(total)


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "action": event.action,
        "outcome": event.outcome,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "request_id": event.request_id,
        "details": dict(event.details or {}),
    }


__all__ = ["ACTIONS", "AuditService", "PruneResult", "event_to_dict"]
