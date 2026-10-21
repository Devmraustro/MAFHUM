"""Settings validation tests."""

from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_safe_for_local_development():
    s = Settings()
    assert s.ai_provider == "mock"
    assert s.api_prefix == "/api/v1"
    assert s.max_upload_mb == 20
    assert s.app_env == "development"


def test_invalid_app_env_is_rejected():
    try:
        Settings(app_env="production-ok")  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid APP_ENV was accepted")


def test_non_mock_provider_is_rejected_in_phase_1():
    try:
        Settings(ai_provider="groq")  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("non-mock AI_PROVIDER was accepted in Phase 1")


def test_positive_limits_enforced():
    try:
        Settings(max_upload_mb=0)  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("non-positive MAX_UPLOAD_MB was accepted")
