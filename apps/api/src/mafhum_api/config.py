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
    # Emit a lightweight liveness ping before each pooled connection checkout.
    # Intended for serverless/managed-Postgres deployments where connections
    # can go stale between invocations; off by default to keep the
    # long-lived-container behaviour (and its per-request latency) unchanged.
    db_pool_pre_ping: bool = False
    # Bound how long establishing a PostgreSQL connection may take. Pure
    # fail-fast improvement: reachable databases connect well inside this;
    # unreachable ones abort instead of hanging a request indefinitely.
    db_connect_timeout_seconds: int = 10
    # Recycle pooled connections older than this many seconds. -1 (disabled)
    # preserves the long-lived-container default; managed providers that close
    # idle connections (e.g. Neon) should set it just below the provider's
    # idle limit. Pair with DB_POOL_PRE_PING=true on serverless.
    db_pool_recycle_seconds: int = -1

    # --- private blob storage -------------------------------------------------
    # Files live OUTSIDE any static/public directory and are never served directly.
    storage_backend: Literal["local", "vercel_blob"] = "local"
    # Credentials for the private Blob backend. Exactly one mode is valid:
    # - Off Vercel (local dev, containers, CI): a read-write token
    #   ("vercel_blob_rw_<store>_<secret>") is required.
    # - On Vercel: either the token, or BLOB_STORE_ID + the function's OIDC
    #   identity (the SDK resolves those itself).
    # These are real Settings fields so the documented template, the fail-fast
    # checks below, and the SDK credential resolution all describe one surface;
    # the adapter passes the token to the SDK explicitly, so a token that only
    # exists in a local `.env` file is honoured too.
    blob_read_write_token: str = ""
    blob_store_id: str = ""
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
    # Chunked uploads (Vercel-safe path for files above the 4.5 MB request
    # body limit). Parts are bounded so any single request stays small; the
    # session expires and its stored parts are reclaimed when abandoned.
    upload_part_size_bytes: int = 4 * 1024 * 1024
    upload_session_ttl_minutes: int = 60 * 24
    cookie_secure: bool = False
    # Login throttle. Counted in the database, so the limit holds across every
    # API process rather than per worker.
    login_throttle_window_seconds: int = 60
    login_throttle_max_attempts: int = 10
    # Throttle for the *unauthenticated* mail-sending endpoints (password reset,
    # address verification). Deliberately tighter than the login window: each
    # allowed call costs a real email, and on a free tier an abused endpoint takes
    # recovery down for everyone. Five per 15 minutes is far above what a human
    # needs and far below what a script would want.
    request_throttle_window_seconds: int = 900
    request_throttle_max_attempts: int = 5
    # How many proxies sit between the client and this process. 1 = one edge that
    # sets X-Forwarded-For (Vercel, or Next's server in the compose stack). Two
    # hops (e.g. CDN -> load balancer -> API) must say 2, otherwise every client
    # resolves to the CDN address and all of them share a single rate-limit bucket.
    trusted_proxy_hops: int = 1
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
    # Set false on every replica except the one that should process documents.
    # The single-writer lease makes running a second worker fail fast; this
    # is the supported way to scale out instead.
    processing_worker_enabled: bool = True
    # Serverless processing driver: when the resident worker is disabled,
    # authenticated "ticks" drive processing instead. Bounded per pass.
    processing_tick_max_jobs: int = 8
    processing_tick_budget_seconds: float = 25.0
    # Bearer token for the unattended (scheduler) tick endpoint. Empty (the
    # default) disables that endpoint entirely; session users can still tick.
    processing_tick_token: str = ""
    processing_worker_count: int = 2
    processing_job_timeout_seconds: int = 120
    processing_stale_after_seconds: int = 300
    processing_max_attempts: int = 3
    processing_startup_recovery: bool = True

    # --- account lifecycle -----------------------------------------------------
    # Password reset and address verification both depend on outbound email, so
    # they are opt-in. With the default (disabled) configuration the API still
    # serves the endpoints, but refuses to start a reset with an explicit,
    # honest "email delivery is not configured" instead of pretending a message
    # was sent. Nothing silently no-ops.
    email_delivery: Literal["disabled", "file", "smtp"] = "disabled"
    # Where the "file" transport writes rendered messages (development only).
    # One .eml per message, mode 0600, inside a gitignored directory. This is
    # what replaces "print the reset link to the log": the log is redacted on
    # purpose, so a real token cannot travel through it.
    email_file_dir: Path = Path("./.mail")
    # Public origin used to build links in email. Never inferred from the Host
    # header: an attacker controls that header, and the resulting link is
    # emailed to the user.
    public_web_url: str = "http://localhost:3000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # "starttls" (587), "ssl" (465) or "plain" (local relay only).
    smtp_security: Literal["starttls", "ssl", "plain"] = "starttls"
    smtp_from_address: str = "no-reply@mafhum.local"
    smtp_timeout_seconds: float = 15.0
    # Tokens are single-use and short-lived; minutes.
    password_reset_ttl_minutes: int = 60
    email_verification_ttl_minutes: int = 60 * 24 * 3
    # Outbox delivery pass: bounded so one scheduled run cannot loop forever.
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5

    # --- concurrency guards ---------------------------------------------------
    max_concurrent_uploads_per_user: int = 3

    # --- presentations (Phase 6) ----------------------------------------------
    # Generation is synchronous but bounded: caps on evidence, output size and
    # wall-clock time keep a single request from exhausting the API process.
    max_presentations_per_user: int = 20
    presentation_max_citations: int = 6
    presentation_max_output_mb: int = 8

    # --- account data export ---------------------------------------------------
    # A ZIP of the caller's own rows and original blobs. Every one of these caps
    # is enforced while the archive is built, not afterwards: an account at the
    # upload quota must not be able to turn one GET into an unbounded read.
    export_max_files: int = 200
    export_max_file_bytes_mb: int = 32
    export_max_total_bytes_mb: int = 128
    export_spool_memory_mb: int = 4
    export_max_seconds: float = 60.0

    # --- audit-log retention ---------------------------------------------------
    # 0 means "keep everything" (the historical behaviour). A positive value is
    # a retention *window in days* applied only to audit_events rows by an
    # explicitly invoked, bounded purge command; nothing purges automatically.
    audit_retention_days: int = 0
    audit_purge_batch_size: int = 1000
    audit_purge_max_batches: int = 10

    # --- scoped monitoring -----------------------------------------------------
    # Empty disables bearer-token access to /admin/metrics entirely (the admin
    # session path still works). A non-empty value must be a high-entropy secret
    # supplied by the operator; it grants access to metrics ONLY, never to any
    # other admin route or to user content.
    metrics_scrape_token: str = ""

    # --- misc -----------------------------------------------------------------
    request_timeout_seconds: int = 30
    default_page_size: int = 20
    max_page_size: int = 100
    enable_ai_gateway: bool = False
    # External source retrieval is opt-in. Off by default so core MAFHUM needs
    # no outbound network at all; enabling it does not enable any paid service.
    research_retrieval_enabled: bool = False

    # --- malware scanning ------------------------------------------------------
    # No engine ships with MAFHUM. Point these at a running clamd daemon to turn
    # scanning on; leaving them empty keeps the disclosed "not scanning" state.
    clamd_unix_path: str | None = None
    clamd_host: str | None = None
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 10.0
    # When true, an upload is REJECTED unless a real engine vouches for it. This
    # is the fail-closed switch: with it on, an unreachable daemon blocks uploads
    # rather than letting unscanned files into the processing path.
    malware_scan_required: bool = False

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

    @field_validator(
        "max_presentations_per_user",
        "presentation_max_citations",
        "presentation_max_output_mb",
    )
    @classmethod
    def _positive_presentation_limits(cls, v: int) -> int:
        if v < 1:
            raise ValueError("presentation limits must be positive")
        return v

    @field_validator("max_archive_compression_ratio")
    @classmethod
    def _positive_ratio(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_archive_compression_ratio must be positive")
        return v

    @field_validator(
        "export_max_files",
        "export_max_file_bytes_mb",
        "export_max_total_bytes_mb",
        "export_spool_memory_mb",
        "audit_purge_batch_size",
        "audit_purge_max_batches",
    )
    @classmethod
    def _positive_bound(cls, v: int) -> int:
        if v < 1:
            raise ValueError("bounded-operation settings must be >= 1")
        return v

    @field_validator("audit_retention_days")
    @classmethod
    def _non_negative_retention(cls, v: int) -> int:
        if v < 0:
            raise ValueError("audit_retention_days must be >= 0 (0 keeps everything)")
        return v

    @field_validator("export_max_seconds")
    @classmethod
    def _positive_deadline(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("export_max_seconds must be positive")
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

    @property
    def export_max_file_bytes(self) -> int:
        return self.export_max_file_bytes_mb * 1024 * 1024

    @property
    def export_max_total_bytes(self) -> int:
        return self.export_max_total_bytes_mb * 1024 * 1024

    @property
    def email_delivery_configured(self) -> bool:
        """True when this deployment can actually deliver mail.

        ``log`` is a real delivery mode (messages are written to the application
        log for local development), so it counts. ``smtp`` counts only when a
        host was supplied.
        """
        if self.email_delivery == "file":
            return True
        if self.email_delivery == "smtp":
            return bool(self.smtp_host.strip())
        return False

    @field_validator("smtp_port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("smtp_port must be between 1 and 65535")
        return v

    @field_validator(
        "password_reset_ttl_minutes",
        "email_verification_ttl_minutes",
        "outbox_batch_size",
        "outbox_max_attempts",
    )
    @classmethod
    def _positive_lifecycle_bound(cls, v: int) -> int:
        if v < 1:
            raise ValueError("account lifecycle bounds must be >= 1")
        return v

    @field_validator("public_web_url")
    @classmethod
    def _valid_public_url(cls, v: str) -> str:
        stripped = v.strip().rstrip("/")
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("public_web_url must be an absolute http(s) URL")
        return stripped

    def assert_production_ready(self) -> None:
        """Refuse to boot in production with development defaults.

        The malware and metrics checks are *preconditions*, not capability
        claims: passing them does not prove a daemon is reachable or that a
        scraper is configured. It proves the deployment cannot silently fall
        back to the unscanned / unauthenticated-metrics state.
        """
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.session_secret == "dev-only-insecure-change-me":  # noqa: S105
            problems.append("SESSION_SECRET must be set in production")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if "localhost" in self.database_url:
            problems.append("DATABASE_URL must not point at localhost in production")
        if self.email_delivery == "file":
            problems.append(
                "EMAIL_DELIVERY=file writes messages to disk and must not be used in production"
            )
        if not self.malware_scan_required:
            problems.append("MALWARE_SCAN_REQUIRED must be true in production")
        elif not (self.clamd_host or self.clamd_unix_path):
            problems.append("CLAMD_HOST or CLAMD_UNIX_PATH must be set in production")
        if len(self.metrics_scrape_token) < 32:
            problems.append("METRICS_SCRAPE_TOKEN must be at least 32 characters in production")
        if self.storage_backend == "vercel_blob" and not (
            self.blob_read_write_token or self.blob_store_id
        ):
            problems.append(
                "BLOB_READ_WRITE_TOKEN or BLOB_STORE_ID must be set in production "
                "when STORAGE_BACKEND=vercel_blob"
            )
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
