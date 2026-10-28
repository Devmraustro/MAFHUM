"""Audit-log retention: bounded, opt-in, and never a data-loss surprise."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mafhum_api.db.models import AuditEvent, User
from mafhum_api.services.audit.service import AuditService


def _seed(db: Session, *, now: datetime, old: int, recent: int) -> None:
    for index in range(old):
        db.add(
            AuditEvent(
                action="auth_login",
                outcome="success",
                created_at=now - timedelta(days=45 + index),
            )
        )
    for index in range(recent):
        db.add(
            AuditEvent(
                action="auth_login",
                outcome="success",
                created_at=now - timedelta(days=1 + index),
            )
        )
    db.commit()


def _count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(AuditEvent)) or 0)


class TestRetentionPolicy:
    def test_zero_days_keeps_everything(
        self, app: FastAPI, db: Session, clean_tables: None
    ) -> None:
        now = datetime.now(UTC)
        _seed(db, now=now, old=3, recent=1)
        result = AuditService().prune(
            app.state.context.database, retention_days=0, batch_size=2, max_batches=5
        )
        assert result.deleted == 0
        assert result.cutoff is None
        assert result.reason == "retention_disabled"
        assert _count(db) == 4

    def test_dry_run_deletes_nothing(self, app: FastAPI, db: Session, clean_tables: None) -> None:
        now = datetime.now(UTC)
        _seed(db, now=now, old=5, recent=1)
        result = AuditService().prune(
            app.state.context.database,
            retention_days=30,
            batch_size=2,
            max_batches=10,
            now=now,
            dry_run=True,
        )
        assert result.dry_run is True
        assert result.deleted == 2  # one batch estimate
        assert result.truncated is True
        assert _count(db) == 6, "dry run must not write or delete"

    def test_cutoff_removes_only_rows_older_than_the_window(
        self, app: FastAPI, db: Session, clean_tables: None
    ) -> None:
        now = datetime.now(UTC)
        _seed(db, now=now, old=3, recent=2)
        result = AuditService().prune(
            app.state.context.database,
            retention_days=30,
            batch_size=10,
            max_batches=10,
            now=now,
        )
        assert result.deleted == 3
        assert result.truncated is False
        db.expire_all()
        remaining = db.scalars(select(AuditEvent)).all()
        assert len(remaining) == 3  # 2 recent + the purge record
        assert all(event.created_at >= now - timedelta(days=30) for event in remaining)
        purge = [event for event in remaining if event.action == "audit_events_purged"]
        assert len(purge) == 1
        assert purge[0].details == {"count": 3, "status": "ok"}
        assert purge[0].actor_id is None

    def test_a_batch_cap_leaves_the_rest_for_a_later_run(
        self, app: FastAPI, db: Session, clean_tables: None
    ) -> None:
        now = datetime.now(UTC)
        _seed(db, now=now, old=6, recent=0)
        result = AuditService().prune(
            app.state.context.database,
            retention_days=30,
            batch_size=2,
            max_batches=1,
            now=now,
        )
        assert result.deleted == 2
        assert result.batches == 1
        assert result.truncated is True
        assert result.reason == "batch_limit_reached"
        db.expire_all()
        assert _count(db) == 5  # 4 old remain + 1 purge record

    def test_no_other_table_is_touched(
        self, app: FastAPI, db: Session, clean_tables: None
    ) -> None:
        # A row in an unrelated table must survive a retention run: this is the
        # concrete guarantee behind "non-destructive".
        unrelated = User(email="retention@example.com", password_hash="scrypt$not-a-real-hash")
        db.add(unrelated)
        db.commit()
        user_id = unrelated.id
        now = datetime.now(UTC)
        _seed(db, now=now, old=2, recent=0)
        AuditService().prune(
            app.state.context.database,
            retention_days=30,
            batch_size=10,
            max_batches=10,
            now=now,
        )
        db.expire_all()
        assert db.get(User, user_id) is not None
        assert int(db.scalar(select(func.count()).select_from(User)) or 0) == 1

    def test_negative_or_zero_bounds_are_rejected(self, app: FastAPI, clean_tables: None) -> None:
        service = AuditService()
        for kwargs in ({"retention_days": -1}, {"retention_days": 30, "batch_size": 0}):
            try:
                service.prune(app.state.context.database, **kwargs)  # type: ignore[arg-type]
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for {kwargs}")


class TestCliDefaults:
    def test_cli_is_dry_run_by_default(
        self,
        app: FastAPI,
        db: Session,
        clean_tables: None,
        monkeypatch,
        settings,  # type: ignore[no-untyped-def]
    ) -> None:
        from tools import purge_audit

        monkeypatch.setattr(purge_audit, "get_settings", lambda: settings)
        now = datetime.now(UTC)
        _seed(db, now=now, old=3, recent=0)
        assert purge_audit.main(["--days", "30", "--batch-size", "10"]) == 0
        db.expire_all()
        assert _count(db) == 3, "the CLI must not delete without --apply"

    def test_cli_rejects_a_negative_window(self, monkeypatch, settings) -> None:  # type: ignore[no-untyped-def]
        from tools import purge_audit

        monkeypatch.setattr(purge_audit, "get_settings", lambda: settings)
        try:
            purge_audit.main(["--days", "-1"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse to reject a negative window")
