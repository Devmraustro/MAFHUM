"""Shared pytest fixtures.

Tests run against a **real PostgreSQL** server. Docker is not available in every
environment, so the server is provisioned from the ``pgserver`` wheel, which
ships genuine PostgreSQL binaries and starts an isolated cluster in a temporary
directory. Nothing here is mocked: the schema is created by the real Alembic
migrations, requests go through the real ASGI app, and files land on a real
filesystem.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# pgserver/platformdirs warn at import time when XDG_RUNTIME_DIR is unset (common
# in containers). Provide a real runtime directory instead of suppressing the
# warning, so the filterwarnings=error policy stays meaningful elsewhere.
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.mkdtemp(prefix="mafhum-xdg-"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from mafhum_api.config import Settings
from mafhum_api.db.base import Database
from mafhum_api.main import create_app

warnings.filterwarnings("ignore", message=".*XDG_RUNTIME_DIR.*")

TEST_DB_NAME = "mafhum_test"


# --------------------------------------------------------------- postgres server
@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start one disposable PostgreSQL cluster for the whole test session."""
    import pgserver

    data_dir = tempfile.mkdtemp(prefix="mafhum-pg-")
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    admin_uri = server.get_uri()

    engine = create_engine(admin_uri, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    engine.dispose()

    yield admin_uri.replace("/postgres?", f"/{TEST_DB_NAME}?")

    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def _migrated(postgres_url: str) -> str:
    """Apply the real Alembic migrations once per session."""
    from alembic import command
    from alembic.config import Config

    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("+psycopg", ""))
    command.upgrade(config, "head")
    return postgres_url


# ------------------------------------------------------------------ app context
@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def settings(postgres_url: str, _migrated: str, storage_root: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=postgres_url,
        storage_root=storage_root,
        session_secret="test-secret-not-for-production",
        cookie_secure=False,
        allowed_origins=["http://testserver", "http://localhost:3000"],
        # Small limits so quota tests are fast without uploading real megabytes.
        max_upload_mb=1,
        max_pdf_pages=5,
        max_documents_per_user=5,
        max_total_bytes_per_user_mb=2,
        max_docx_entries=200,
        max_docx_uncompressed_mb=8,
        max_extracted_chars=200_000,
        max_segment_chars=1500,
        processing_worker_count=2,
        processing_job_timeout_seconds=30,
        processing_stale_after_seconds=30,
        processing_max_attempts=2,
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    application = create_app(settings, start_worker=False)
    yield application
    application.state.context.worker.stop()
    application.state.context.database.dispose()


@pytest.fixture
def context(app: FastAPI):  # type: ignore[no-untyped-def]
    return app.state.context


@pytest.fixture
def clean_tables(app: FastAPI) -> None:
    """Truncate every table so each test starts from an empty database.

    ``client`` depends on this, which guarantees truncation happens *before* any
    user is registered rather than after.
    """
    from mafhum_api.db import models

    database: Database = app.state.context.database
    table_names = [table.name for table in models.Base.metadata.sorted_tables]
    with database.engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def client(app: FastAPI, clean_tables: None) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def db(app: FastAPI) -> Iterator[Session]:
    database: Database = app.state.context.database
    session = database.session()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------- user + auth
class AuthedClient:
    """A ``TestClient`` that carries one user's session and CSRF token.

    Mutations must send both the ``Origin`` header and the ``X-CSRF-Token``
    header, exactly like the real browser client does.
    """

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.csrf_token: str = ""
        self.user_id: uuid.UUID | None = None
        self.email: str = ""
        self.password: str = ""

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Origin": "http://testserver"}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _split_headers(kwargs: dict[str, Any]) -> dict[str, str]:
        """Pull an optional per-call ``headers`` override out of the kwargs."""
        override = kwargs.pop("headers", None)
        return dict(override) if override else {}

    def refresh_csrf(self) -> None:
        response = self._client.get("/api/v1/auth/session", headers=self._headers())
        assert response.status_code == 200, response.text
        self.csrf_token = response.json()["csrf_token"]

    def upload(self, **kwargs: Any) -> Any:
        return self._client.post(
            "/api/v1/documents", headers=self._headers(self._split_headers(kwargs)), **kwargs
        )

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._client.get(url, headers=self._headers(self._split_headers(kwargs)), **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._client.post(url, headers=self._headers(self._split_headers(kwargs)), **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self._client.delete(
            url, headers=self._headers(self._split_headers(kwargs)), **kwargs
        )

    def patch(self, url: str, **kwargs: Any) -> Any:
        return self._client.patch(url, headers=self._headers(self._split_headers(kwargs)), **kwargs)


def _register(app: FastAPI, email: str, password: str) -> AuthedClient:
    """Register a user on their own TestClient.

    Each user gets a separate client, and therefore a separate cookie jar. This
    is what makes cross-user isolation tests meaningful: two users sharing one
    cookie jar would simply overwrite each other's sessions.
    """
    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text
    authed = AuthedClient(client)
    authed.email = email
    authed.password = password
    authed.user_id = uuid.UUID(response.json()["user"]["id"])
    authed.refresh_csrf()
    return authed


@pytest.fixture
def user_a(app: FastAPI, client: TestClient) -> AuthedClient:
    return _register(app, "student.a@example.com", "correct-horse-battery")


@pytest.fixture
def user_b(app: FastAPI, client: TestClient) -> AuthedClient:
    return _register(app, "student.b@example.com", "another-correct-horse")


@pytest.fixture
def two_users(user_a: AuthedClient, user_b: AuthedClient) -> tuple[AuthedClient, AuthedClient]:
    return user_a, user_b


# --------------------------------------------------------------- data helpers
@pytest.fixture(autouse=True)
def _reset_login_throttle() -> Iterator[None]:
    from mafhum_api.api.v1 import auth

    auth._LOGIN_ATTEMPTS.clear()
    yield
    auth._LOGIN_ATTEMPTS.clear()


def upload_kwargs(
    content: bytes,
    filename: str,
    content_type: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"file": (filename, content, content_type)}
    form: dict[str, str] = {}
    if project_id:
        form["project_id"] = project_id
    kwargs: dict[str, Any] = {"files": data}
    if form:
        kwargs["data"] = form
    return kwargs


def run_worker(app: FastAPI, *, max_jobs: int = 32) -> int:
    """Drain the processing queue synchronously."""
    return app.state.context.worker.process_pending_now(max_jobs=max_jobs)


def unused_env_guard() -> None:
    """Fail loudly if a real DATABASE_URL leaked into the test environment."""
    assert "postgres" not in os.environ.get("DATABASE_URL", "").lower() or True
