"""Pytest configuration and isolated SQLite + real PostgreSQL test fixtures."""

import os
import tempfile
from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_NAME", "MAFHUM")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("API_PREFIX", "/api/v1")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("AI_MODEL", "mock")
os.environ.setdefault("MAX_UPLOAD_MB", "20")
os.environ.setdefault("MAX_DOCUMENT_PAGES", "200")
os.environ.setdefault("MAX_OUTPUT_TOKENS", "2000")
os.environ.setdefault("REQUEST_TIMEOUT_SECONDS", "30")
os.environ.setdefault("ALLOW_DEV_ORIGINS", "http://localhost:3000,http://testserver")

_BOOTSTRAP_DB = os.path.join(tempfile.gettempdir(), "mafhum_api_bootstrap.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_BOOTSTRAP_DB}"

import pgserver  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import app.api.deps as deps_module  # noqa: E402
import app.config as config_module  # noqa: E402
from app.main import app  # noqa: E402


def _upgrade_sqlite_db(db_path: str) -> None:
    url = f"sqlite:///{db_path}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    previous = config_module.settings.database_url
    config_module.settings.database_url = url
    try:
        command.upgrade(cfg, "head")
    finally:
        config_module.settings.database_url = previous


if os.path.exists(_BOOTSTRAP_DB):
    os.remove(_BOOTSTRAP_DB)
_upgrade_sqlite_db(_BOOTSTRAP_DB)


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def migrated_env(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Create a fresh, Alembic-migrated SQLite DB and reset all rate limiters."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        url = f"sqlite:///{db_path}"
        monkeypatch.setattr(config_module.settings, "database_url", url)
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        deps_module.reset_all_limiters()
        yield db_path
        if deps_module._engine is not None:
            deps_module._engine.dispose()


@pytest.fixture(scope="session")
def postgres_base_url() -> Generator[str, None, None]:
    """Return a live PostgreSQL server URL (external CI service or embedded pgserver)."""
    external = os.environ.get("TEST_POSTGRES_URL")
    if external:
        yield external
        return
    pg_dir = tempfile.mkdtemp(prefix="mafhum_pgserver_")
    server = pgserver.get_server(pg_dir)  # type: ignore[attr-defined]
    try:
        uri: str = server.get_uri()
        if uri.startswith("postgresql://"):
            uri = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        yield uri
    finally:
        server.cleanup()


@pytest.fixture()
def postgres_empty_db(
    postgres_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[str, None, None]:
    """Create a brand-new empty PostgreSQL database for isolated tests."""
    import uuid

    db_name = f"mafhum_test_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(postgres_base_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    base_url_obj = make_url(postgres_base_url)
    test_url = base_url_obj.set(database=db_name).render_as_string(
        hide_password=False
    )
    monkeypatch.setattr(config_module.settings, "database_url", test_url)
    deps_module.reset_all_limiters()
    try:
        yield test_url
    finally:
        if deps_module._engine is not None:
            deps_module._engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db_name AND pid <> pg_backend_pid()"
                ),
                {"db_name": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin_engine.dispose()
