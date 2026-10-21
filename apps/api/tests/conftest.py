"""Pytest configuration and isolated Alembic-migrated test database fixtures."""

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
os.environ.setdefault("ALLOW_DEV_ORIGINS", "http://localhost:3000")

_BOOTSTRAP_DB = os.path.join(tempfile.gettempdir(), "mafhum_api_bootstrap.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_BOOTSTRAP_DB}"

from fastapi.testclient import TestClient  # noqa: E402

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


# Ensure the default DB for legacy Phase 1 `client()` calls is cleanly migrated.
if os.path.exists(_BOOTSTRAP_DB):
    os.remove(_BOOTSTRAP_DB)
_upgrade_sqlite_db(_BOOTSTRAP_DB)


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def migrated_env(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Create a fresh, Alembic-migrated SQLite DB and reset rate limiters."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        url = f"sqlite:///{db_path}"
        monkeypatch.setattr(config_module.settings, "database_url", url)
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        deps_module.register_limiter.reset()
        deps_module.login_limiter.reset()
        yield db_path
        if deps_module._engine is not None:
            deps_module._engine.dispose()
