"""Validated application configuration.

Every operational limit that the document pipeline enforces lives here so it can
be tuned per environment without touching business logic. Defaults are safe for
a modest laptop (no GPU, 4 GB RAM).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application identity -------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "MAFHUM"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- persistence ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://mafhum:change_me@localhost:5432/mafhum"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- private blob storage -------------------------------------------------
    # Files live OUTSIDE any static/public directory and are never served directly.
    storage_backend: Literal["local"] = "local"
    storage_root: Path = Path("./data/mafhum-storage")
    storage_dir_mode: int = 0o700
    storage_file_mode: int = 0o600

    # --- session / auth -------------------------------------------------------
    # A development fallback exists so the skeleton boots locally; production
    # refuses to start without an explicit secret.
    # Deliberate development fallback; assert_production_ready() refuses to
    # boot when this value is still present in production.
    session_secret: str = "dev-only-insecure-change-me"  # noqa: S105
    session_cookie_name: str = "mafhum_session"
    csrf_cookie_name: str = "mafhum_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_ttl_minutes: int = 60 * 24 * 7
    cookie_secure: bool = False
    # Login throttle. Counted in the database, so the limit holds across every
    # API process rather than per worker.
    login_throttle_window_seconds: int = 60
    login_throttle_max_attempts: int = 10
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- upload limits (server-authoritative) ---------------------------------
    max_upload_mb: int = 20
    max_extracted_chars: int = 5_000_000
    max_pdf_pages: int = 200
    max_docx_entries: int = 400
    max_docx_uncompressed_mb: int = 60
    max_archive_compression_ratio: float = 100.0
    max_documents_per_user: int = 50
    max_total_bytes_per_user_mb: int = 500
    max_segment_chars: int = 4000

    # --- processing pipeline --------------------------------------------------
    processing_worker_count: int = 2
    processing_job_timeout_seconds: int = 120
    processing_stale_after_seconds: int = 300
    processing_max_attempts: int = 3
    processing_startup_recovery: bool = True

    # --- concurrency guards ---------------------------------------------------
    max_concurrent_uploads_per_user: int = 3

    # --- misc -----------------------------------------------------------------
    request_timeout_seconds: int = 30
    default_page_size: int = 20
    max_page_size: int = 100
    enable_ai_gateway: bool = False
    # External source retrieval is opt-in. Off by default so core MAFHUM needs
    # no outbound network at all; enabling it does not enable any paid service.
    research_retrieval_enabled: bool = False

    @field_validator("max_upload_mb", "max_docx_uncompressed_mb", "max_total_bytes_per_user_mb")
    @classmethod
    def _positive_mb(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("byte-budget settings must be positive")
        return v

    @field_validator("processing_worker_count")
    @classmethod
    def _positive_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("processing_worker_count must be >= 1")
        return v

    @field_validator("max_archive_compression_ratio")
    @classmethod
    def _positive_ratio(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_archive_compression_ratio must be positive")
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_docx_uncompressed_bytes(self) -> int:
        return self.max_docx_uncompressed_mb * 1024 * 1024

    @property
    def max_total_bytes_per_user(self) -> int:
        return self.max_total_bytes_per_user_mb * 1024 * 1024

    def assert_production_ready(self) -> None:
        """Refuse to boot in production with development defaults."""
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.session_secret == "dev-only-insecure-change-me":  # noqa: S105
            problems.append("SESSION_SECRET must be set in production")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if "localhost" in self.database_url:
            problems.append("DATABASE_URL must not point at localhost in production")
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
