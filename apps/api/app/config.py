"""Validated environment configuration for MAFHUM.

Loaded once at import time. `.env.example` documents every variable; real
values live in `.env` (never committed) or the deployment environment.
"""


from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    app_name: str = "MAFHUM"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    database_url: str | None = None

    ai_provider: str = "mock"
    ai_model: str = "mock"

    max_upload_mb: int = 20
    max_document_pages: int = 200
    max_output_tokens: int = 2000
    request_timeout_seconds: int = 30

    allow_dev_origins: str = "http://localhost:3000"

    @field_validator("app_env")
    @classmethod
    def _env(cls, value: str) -> str:
        allowed = {"development", "staging", "production"}
        if value not in allowed:
            raise ValueError(f"APP_ENV must be one of {sorted(allowed)}")
        return value

    @field_validator("ai_provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        # Phase 1: only the deterministic mock provider exists. Other vendors
        # are added through the provider interface, never hard-coded here.
        if value != "mock":
            raise ValueError(
                f"unsupported AI_PROVIDER '{value}'; only 'mock' is implemented in Phase 1"
            )
        return value

    @field_validator(
        "max_upload_mb",
        "max_document_pages",
        "max_output_tokens",
        "request_timeout_seconds",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit must be positive")
        return value


settings = Settings()
