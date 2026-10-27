"""Phase 7 — admin role, append-only audit trail and redacted monitoring.

The important properties are negative: a normal user cannot tell the admin
routes exist, no HTTP call can grant a role or delete an event, and the audit
log never becomes a second copy of personal data.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tools.set_admin import set_role

from mafhum_api.db.models import AuditEvent, User, UserRole
from tests.conftest import AuthedClient


def promote(app: FastAPI, user_id: uuid.UUID) -> None:
    with app.state.context.database.session() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.role = UserRole.ADMIN.value
        db.commit()


class TestRoleGate:
    def test_non_admin_gets_the_same_404_as_a_missing_resource(self, user_a: AuthedClient) -> None:
        for path in (
            "/api/v1/admin/overview",
            "/api/v1/admin/usage",
            "/api/v1/admin/audit-events",
            "/api/v1/admin/metrics",
        ):
            response = user_a.get(path)
            assert response.status_code == 404, (path, response.text)
            assert response.json()["code"] == "resource_not_found"

    def test_denial_is_recorded_even_though_the_request_failed(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI
    ) -> None:
        user_a, _ = two_users
        assert user_a.get("/api/v1/admin/overview").status_code == 404
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        events = user_a.get("/api/v1/admin/audit-events").json()["items"]
        denials = [event for event in events if event["action"] == "admin_access_denied"]
        assert len(denials) >= 1
        assert denials[0]["outcome"] == "denied"
        assert denials[0]["details"] == {"reason": "role_not_admin"}

    def test_no_http_endpoint_can_change_a_role_or_delete_an_event(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        for method, path in (
            ("post", "/api/v1/admin/overview"),
            ("delete", "/api/v1/admin/audit-events"),
            ("patch", "/api/v1/admin/usage"),
        ):
            response = getattr(user_a, method)(path)
            assert response.status_code == 405, (method, path, response.text)

    def test_database_rejects_an_unknown_role(self, app: FastAPI, user_a: AuthedClient) -> None:
        with app.state.context.database.session() as db:
            user = db.get(User, user_a.user_id)
            assert user is not None
            user.role = "superadmin"
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            else:
                raise AssertionError("the CHECK constraint did not reject the role")


class TestAdminViews:
    def test_overview_and_usage_have_no_document_content(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        overview = user_a.get("/api/v1/admin/overview")
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert body["active_users"] >= 1
        assert isinstance(body["documents_by_status"], dict)

        usage = user_a.get("/api/v1/admin/usage")
        assert usage.status_code == 200, usage.text
        assert usage.json()["total"] >= 1
        assert user_a.email in usage.text  # an operator must identify accounts
        assert "password_hash" not in usage.text
        assert "storage_key" not in usage.text

    def test_metrics_are_counters_only(self, user_a: AuthedClient, app: FastAPI) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        response = user_a.get("/api/v1/admin/metrics")
        assert response.status_code == 200
        assert "mafhum_active_users" in response.text
        assert user_a.email not in response.text
        assert "@" not in response.text

    def test_audit_trail_is_readable_and_filterable(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        # Registration and the denied probes above already produced events.
        user_a.get("/api/v1/admin/overview")
        all_events = user_a.get("/api/v1/admin/audit-events").json()
        assert all_events["total"] >= 1
        filtered = user_a.get("/api/v1/admin/audit-events?action=admin_overview_viewed").json()
        assert filtered["total"] >= 1
        assert all(e["action"] == "admin_overview_viewed" for e in filtered["items"])


class TestAuditHygiene:
    def test_failed_login_is_recorded_without_credentials(
        self, app: FastAPI, client: object
    ) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app, base_url="http://testserver") as anonymous:
            response = anonymous.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "TopSecret-123"},
                headers={"Origin": "http://testserver"},
            )
            assert response.status_code == 401
        with app.state.context.database.session() as db:
            events = db.scalars(
                select(AuditEvent).where(AuditEvent.action == "auth_login_failed")
            ).all()
            assert events, "a rejected login must be visible to an admin"
            serialized = str([event.details for event in events])
            assert "TopSecret-123" not in serialized
            assert "nobody@example.com" not in serialized

    def test_audit_details_are_allow_listed(self, user_a: AuthedClient, app: FastAPI) -> None:
        with app.state.context.database.session() as db:
            context = app.state.context
            event = context.audit.record(
                db,
                action="admin_overview_viewed",
                actor_id=user_a.user_id,  # type: ignore[arg-type]
                details={"reason": "ok", "email": "leak@example.com", "filename": "secret.pdf"},
            )
            assert event.details == {"reason": "ok"}
            db.rollback()

    def test_events_survive_account_deletion_with_a_null_actor(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        assert user_a.get("/api/v1/admin/overview").status_code == 200
        with app.state.context.database.session() as db:
            context = app.state.context
            report = context.deletion.delete_account(db, user_a.user_id)  # type: ignore[arg-type]
            assert report.complete is True
            remaining = db.scalars(
                select(AuditEvent).where(AuditEvent.action == "admin_overview_viewed")
            ).all()
            assert remaining, "the trail must not be erased with the account"
            assert all(event.actor_id is None for event in remaining)


class TestOperatorCli:
    def test_set_role_changes_the_role_and_writes_an_audit_row(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI, postgres_url: str
    ) -> None:
        _user_a, user_b = two_users
        assert user_b.get("/api/v1/admin/overview").status_code == 404
        result = set_role(postgres_url, user_b.email, "admin")
        assert result["role"] == "admin"
        assert result["previous_role"] == "student"
        # The existing session sees the new role on the next request.
        assert user_b.get("/api/v1/admin/overview").status_code == 200
        with app.state.context.database.session() as db:
            rows = db.scalars(
                select(AuditEvent).where(AuditEvent.action == "admin_role_changed")
            ).all()
            assert len(rows) == 1
            assert rows[0].details == {"status": "admin"}


class TestHardeningFollowUps:
    """Fixes found during the post-merge review of PR #3."""

    def test_repeated_denials_do_not_flood_the_audit_table(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI
    ) -> None:
        user_a, _ = two_users
        for _ in range(6):
            assert user_a.get("/api/v1/admin/overview").status_code == 404
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        denials = user_a.get("/api/v1/admin/audit-events?action=admin_access_denied").json()
        assert denials["total"] == 1, denials
        assert denials["items"][0]["details"] == {"reason": "role_not_admin"}

    def test_metrics_view_is_audited(self, user_a: AuthedClient, app: FastAPI) -> None:
        promote(app, user_a.user_id)  # type: ignore[arg-type]
        assert user_a.get("/api/v1/admin/metrics").status_code == 200
        events = user_a.get("/api/v1/admin/audit-events?action=admin_metrics_viewed").json()
        assert events["total"] == 1

    def test_cli_refuses_to_demote_the_last_admin(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI, postgres_url: str
    ) -> None:
        _user_a, user_b = two_users
        set_role(postgres_url, user_b.email, "admin")
        with pytest.raises(SystemExit, match="last active admin"):
            set_role(postgres_url, user_b.email, "student")
        # Still an admin, so the account was not locked out.
        assert user_b.get("/api/v1/admin/overview").status_code == 200
        # An explicit --force is allowed (operator decision, audited).
        result = set_role(postgres_url, user_b.email, "student", force=True)
        assert result["role"] == "student"
        assert user_b.get("/api/v1/admin/overview").status_code == 404
