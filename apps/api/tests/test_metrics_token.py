"""Scoped metrics token: metrics only, never an admin backdoor."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mafhum_api.config import Settings
from mafhum_api.db.models import AuditEvent, User, UserRole
from mafhum_api.main import create_app
from tests.conftest import _register

METRICS = "/api/v1/admin/metrics"
TOKEN = "synthetic-metrics-token-0123456789abcdef"


def _token_app(settings: Settings, tmp_path: Path, token: str = TOKEN) -> FastAPI:
    custom = settings.model_copy(
        update={"metrics_scrape_token": token, "storage_root": tmp_path / "metrics-store"}
    )
    Path(custom.storage_root).mkdir(parents=True, exist_ok=True)
    return create_app(custom, start_worker=False)


def _promote(app: FastAPI, user_id: uuid.UUID) -> None:
    with app.state.context.database.session() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.role = UserRole.ADMIN.value
        db.commit()


def _metric_views(app: FastAPI) -> int:
    with app.state.context.database.session() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "admin_metrics_viewed")
            )
            or 0
        )


class TestNoTokenConfigured:
    def test_bearer_is_rejected_when_no_token_is_set(self, client: TestClient) -> None:
        response = client.get(METRICS, headers={"Authorization": "Bearer anything"})
        assert response.status_code == 401, response.text

    def test_normal_user_still_gets_the_same_404(
        self,
        user_a,
        clean_tables: None,  # type: ignore[no-untyped-def]
    ) -> None:
        assert user_a.get(METRICS).status_code == 404


class TestScopedToken:
    def test_valid_token_reads_metrics_without_any_session(
        self, settings: Settings, tmp_path: Path, clean_tables: None
    ) -> None:
        app = _token_app(settings, tmp_path)
        try:
            with TestClient(app, base_url="http://testserver") as anonymous:
                response = anonymous.get(METRICS, headers={"Authorization": f"Bearer {TOKEN}"})
                assert response.status_code == 200, response.text
                body = response.text
                for name in (
                    "mafhum_up",
                    "mafhum_active_users",
                    "mafhum_malware_scan_required",
                    "mafhum_malware_scanner_available",
                    "mafhum_processing_pending",
                    "mafhum_audit_events_total",
                ):
                    assert name in body, name
                assert "@" not in body
                assert "password" not in body.lower()
        finally:
            app.state.context.database.dispose()

    def test_wrong_token_is_rejected_even_with_an_admin_cookie(
        self, settings: Settings, tmp_path: Path, clean_tables: None
    ) -> None:
        app = _token_app(settings, tmp_path)
        try:
            admin = _register(app, "metrics.admin@example.com", "Passw0rd!23")
            _promote(app, admin.user_id)  # type: ignore[arg-type]
            assert admin.get(METRICS).status_code == 200
            response = admin.get(
                METRICS,
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert response.status_code == 401, response.text
        finally:
            app.state.context.database.dispose()

    def test_token_grants_no_access_to_other_admin_routes(
        self, settings: Settings, tmp_path: Path, clean_tables: None
    ) -> None:
        app = _token_app(settings, tmp_path)
        try:
            with TestClient(app, base_url="http://testserver") as anonymous:
                for path in (
                    "/api/v1/admin/overview",
                    "/api/v1/admin/usage",
                    "/api/v1/admin/audit-events",
                    "/api/v1/me/usage",
                ):
                    response = anonymous.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
                    assert response.status_code in (401, 404), (path, response.text)
                    assert "mafhum_active_users" not in response.text
        finally:
            app.state.context.database.dispose()

    def test_token_scrapes_do_not_flood_the_audit_log(
        self, settings: Settings, tmp_path: Path, clean_tables: None
    ) -> None:
        app = _token_app(settings, tmp_path)
        try:
            before = _metric_views(app)
            with TestClient(app, base_url="http://testserver") as anonymous:
                for _ in range(4):
                    assert (
                        anonymous.get(
                            METRICS, headers={"Authorization": f"Bearer {TOKEN}"}
                        ).status_code
                        == 200
                    )
            assert _metric_views(app) == before, "a scrape must not write an audit row"

            admin = _register(app, "metrics.human@example.com", "Passw0rd!23")
            _promote(app, admin.user_id)  # type: ignore[arg-type]
            assert admin.get(METRICS).status_code == 200
            assert _metric_views(app) == before + 1, "a human admin view must be audited"
        finally:
            app.state.context.database.dispose()

    def test_empty_token_setting_disables_the_bearer_path(
        self, settings: Settings, tmp_path: Path, clean_tables: None
    ) -> None:
        app = _token_app(settings, tmp_path, token="")
        try:
            with TestClient(app, base_url="http://testserver") as anonymous:
                response = anonymous.get(METRICS, headers={"Authorization": "Bearer "})
                assert response.status_code == 401, response.text
        finally:
            app.state.context.database.dispose()
