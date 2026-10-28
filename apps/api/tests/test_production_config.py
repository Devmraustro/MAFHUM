"""Production configuration hardening.

These tests boot the real application with ``app_env="production"`` against the
disposable PostgreSQL cluster and drive it over HTTP, rather than only asserting
that a validation function raises. The distinction matters: a setting can be
validated correctly and still fail to reach the response that depends on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mafhum_api.config import Settings
from mafhum_api.main import create_app

STRONG_SECRET = "production-synthetic-secret-9f3c1ab7d5e2480c"


def production_settings(postgres_url: str, storage_root: Path, **overrides: object) -> Settings:
    """A configuration that *should* be accepted as production-ready."""
    base: dict[str, object] = {
        "app_env": "production",
        "database_url": postgres_url,
        "storage_root": storage_root,
        "session_secret": STRONG_SECRET,
        "cookie_secure": True,
        "log_format": "json",
        # Production now refuses to boot without a real scanning policy, a
        # daemon address and a scoped metrics token. Synthetic values only.
        "malware_scan_required": True,
        "clamd_host": "127.0.0.1",
        "metrics_scrape_token": "synthetic-production-metrics-token-0123456789",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def prod_app(postgres_url: str, _migrated: str, storage_root: Path) -> Iterator[object]:
    settings = production_settings(postgres_url, storage_root)
    application = create_app(settings, start_worker=True)
    yield application
    application.state.context.worker.stop()
    application.state.context.database.dispose()


class TestProductionRefusals:
    """Every insecure default must fail loudly at startup, not silently work."""

    def test_refuses_the_development_session_secret(
        self, postgres_url: str, storage_root: Path
    ) -> None:
        settings = production_settings(
            postgres_url, storage_root, session_secret="dev-only-insecure-change-me"
        )
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            settings.assert_production_ready()

    def test_refuses_insecure_cookies(self, postgres_url: str, storage_root: Path) -> None:
        settings = production_settings(postgres_url, storage_root, cookie_secure=False)
        with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
            settings.assert_production_ready()

    def test_refuses_a_localhost_database(self, storage_root: Path) -> None:
        settings = production_settings(
            "postgresql+psycopg://mafhum:pw@localhost:5432/mafhum", storage_root
        )
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            settings.assert_production_ready()

    def test_refuses_every_problem_at_once(self, postgres_url: str, storage_root: Path) -> None:
        """All misconfigurations are reported together, so an operator fixes
        them in one pass instead of discovering them one restart at a time."""
        settings = production_settings(
            "postgresql+psycopg://mafhum:pw@localhost:5432/mafhum",
            storage_root,
            session_secret="dev-only-insecure-change-me",
            cookie_secure=False,
        )
        with pytest.raises(RuntimeError) as excinfo:
            settings.assert_production_ready()
        message = str(excinfo.value)
        assert "SESSION_SECRET" in message
        assert "COOKIE_SECURE" in message
        assert "DATABASE_URL" in message

    def test_startup_fails_rather_than_serving(self, postgres_url: str, storage_root: Path) -> None:
        """The refusal happens during app construction, so an insecure process
        never reaches the point of accepting a request."""
        settings = production_settings(
            postgres_url, storage_root, session_secret="dev-only-insecure-change-me"
        )
        with pytest.raises(RuntimeError):
            create_app(settings, start_worker=False)

    def test_refuses_unscanned_uploads(self, postgres_url: str, storage_root: Path) -> None:
        settings = production_settings(postgres_url, storage_root, malware_scan_required=False)
        with pytest.raises(RuntimeError, match="MALWARE_SCAN_REQUIRED"):
            settings.assert_production_ready()

    def test_refuses_scan_required_without_a_daemon(
        self, postgres_url: str, storage_root: Path
    ) -> None:
        settings = production_settings(
            postgres_url, storage_root, clamd_host=None, clamd_unix_path=None
        )
        with pytest.raises(RuntimeError, match="CLAMD_HOST"):
            settings.assert_production_ready()

    def test_a_unix_socket_daemon_address_is_acceptable(
        self, postgres_url: str, storage_root: Path
    ) -> None:
        settings = production_settings(
            postgres_url,
            storage_root,
            clamd_host=None,
            clamd_unix_path="/run/clamd/clamd.sock",
        )
        settings.assert_production_ready()

    def test_refuses_a_missing_or_short_metrics_token(
        self, postgres_url: str, storage_root: Path
    ) -> None:
        for token in ("", "short"):
            settings = production_settings(postgres_url, storage_root, metrics_scrape_token=token)
            with pytest.raises(RuntimeError, match="METRICS_SCRAPE_TOKEN"):
                settings.assert_production_ready()

    def test_development_and_test_still_boot(self, postgres_url: str, storage_root: Path) -> None:
        for env in ("development", "test"):
            settings = Settings(
                app_env=env,  # type: ignore[arg-type]
                database_url=postgres_url,
                storage_root=storage_root,
                cookie_secure=False,
            )
            settings.assert_production_ready()  # must not raise


class TestProductionRuntime:
    def test_interactive_docs_are_disabled(self, prod_app: object) -> None:
        with TestClient(prod_app, base_url="https://testserver") as client:  # type: ignore[arg-type]
            assert client.get("/api/docs").status_code == 404
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404

    def test_session_cookies_are_marked_secure(self, prod_app: object) -> None:
        with TestClient(prod_app, base_url="https://testserver") as client:  # type: ignore[arg-type]
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "prod.config@example.com",
                    "password": "Correct-Horse-Battery",
                    "display_name": "طالب",
                },
                headers={"Origin": "http://localhost:3000"},
            )
            assert response.status_code == 201, response.text
            raw = response.headers.get_list("set-cookie")
            session_cookie = next(c for c in raw if c.startswith("mafhum_session="))
            csrf_cookie = next(c for c in raw if c.startswith("mafhum_csrf="))

            # The session cookie must be unreadable by script and HTTPS-only.
            assert "HttpOnly" in session_cookie
            assert "Secure" in session_cookie
            assert "samesite=lax" in session_cookie.lower()

            # The CSRF cookie must stay readable for the double-submit to work,
            # but must not be sent over plaintext either.
            assert "HttpOnly" not in csrf_cookie
            assert "Secure" in csrf_cookie

    def test_security_headers_present(self, prod_app: object) -> None:
        with TestClient(prod_app, base_url="https://testserver") as client:  # type: ignore[arg-type]
            response = client.get("/health/live")
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.headers["cache-control"] == "no-store"
            assert "x-powered-by" not in {k.lower() for k in response.headers}

    def test_health_reports_dependencies(self, prod_app: object) -> None:
        with TestClient(prod_app, base_url="https://testserver") as client:  # type: ignore[arg-type]
            live = client.get("/health/live")
            ready = client.get("/health/ready")
            assert live.status_code == 200
            assert ready.status_code == 200
            checks = ready.json()["checks"]
            assert checks["database"] == "ok"
            assert checks["storage"] == "ok"

    def test_error_envelope_leaks_no_internals(self, prod_app: object) -> None:
        with TestClient(prod_app, base_url="https://testserver") as client:  # type: ignore[arg-type]
            # Authenticate first: unauthenticated this route returns 401, which
            # would prove nothing about the shape of a real error envelope.
            register = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "prod.envelope@example.com",
                    "password": "Correct-Horse-Battery",
                    "display_name": "طالب",
                },
                headers={"Origin": "http://localhost:3000"},
            )
            assert register.status_code == 201, register.text
            response = client.get(
                "/api/v1/documents/00000000-0000-0000-0000-000000000000",
                headers={"Origin": "http://localhost:3000"},
            )
            assert response.status_code == 404
            body = response.json()
            assert set(body) <= {"code", "message", "request_id", "field_errors", "details"}
            assert body["code"] == "resource_not_found"
            assert "Traceback" not in response.text
            assert "sql" not in response.text.lower()
            assert "/home" not in response.text

    def test_worker_runs_and_stops_cleanly(self, prod_app: object) -> None:
        """Graceful shutdown must stop the worker and dispose the engine rather
        than leaving threads holding connections."""
        context = prod_app.state.context  # type: ignore[attr-defined]
        # The worker is started by the lifespan, not at construction time, so it
        # is only live while the app is being served.
        assert context.worker.is_running is False
        with TestClient(prod_app, base_url="https://testserver"):  # type: ignore[arg-type]
            assert context.worker.is_running is True
        # Exiting the context runs the lifespan shutdown: the dispatcher thread
        # must be joined, not left running against a disposed engine.
        assert context.worker.is_running is False
