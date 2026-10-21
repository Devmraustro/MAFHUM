"""Settings and production startup validation tests."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_safe_for_local_development() -> None:
    s = Settings()
    assert s.ai_provider == "mock"
    assert s.api_prefix == "/api/v1"
    assert s.max_upload_mb == 20
    assert s.app_env == "development"


def test_invalid_app_env_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production-ok")


def test_non_mock_provider_is_rejected_in_phase_1() -> None:
    with pytest.raises(ValidationError):
        Settings(ai_provider="groq")


def test_positive_limits_enforced() -> None:
    with pytest.raises(ValidationError):
        Settings(max_upload_mb=0)


def test_production_rejects_default_or_short_secret() -> None:
    s_default = Settings(
        app_env="production",
        database_url="postgresql+psycopg://mafhum:pw@db:5432/mafhum",
    )
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        s_default.validate_for_production()

    s_short = Settings(
        app_env="production",
        session_secret="too-short-secret",
        database_url="postgresql+psycopg://mafhum:pw@db:5432/mafhum",
    )
    with pytest.raises(ValueError, match="32 characters"):
        s_short.validate_for_production()


def test_production_rejects_missing_or_sqlite_db_and_samesite_none() -> None:
    strong_secret = "x" * 40
    s_no_db = Settings(
        app_env="production",
        session_secret=strong_secret,
        database_url=None,
    )
    with pytest.raises(ValueError, match="DATABASE_URL"):
        s_no_db.validate_for_production()

    s_sqlite = Settings(
        app_env="production",
        session_secret=strong_secret,
        database_url="sqlite:////tmp/prod.db",
    )
    with pytest.raises(ValueError, match="SQLite"):
        s_sqlite.validate_for_production()

    s_samesite = Settings(
        app_env="production",
        session_secret=strong_secret,
        database_url="postgresql+psycopg://mafhum:pw@db:5432/mafhum",
        session_cookie_same_site="none",
    )
    with pytest.raises(ValueError, match="SESSION_COOKIE_SAME_SITE"):
        s_samesite.validate_for_production()


def test_production_accepts_hardened_configuration() -> None:
    s = Settings(
        app_env="production",
        session_secret="s" * 48,
        database_url="postgresql+psycopg://mafhum:pw@db:5432/mafhum",
        session_cookie_same_site="lax",
    )
    s.validate_for_production()
