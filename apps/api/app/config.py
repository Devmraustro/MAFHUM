"""Validated environment configuration for MAFHUM.

Loaded once at import time. `.env.example` documents every variable; real
values live in `.env` (never committed) or the deployment environment.
Production settings are validated at startup and the application refuses to
start with an insecure production configuration.
"""

import ipaddress

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
    trusted_proxy_ips: str = ""

    # --- Sessions -------------------------------------------------------
    session_secret: str = "change-me-in-production"
    session_cookie_name: str = "mafhum_session"
    session_cookie_same_site: str = "lax"
    session_ttl_minutes: int = 60 * 24 * 7  # 7 days
    session_max_per_user: int = 10

    # --- Rate limiting (auth endpoints) ---------------------------------
    auth_rate_limit: int = 5  # per-account attempts within window
    auth_ip_rate_limit: int = 10  # independent per-IP attempts within window
    auth_rate_window_seconds: int = 60

    # --- Quotas ---------------------------------------------------------
    default_project_quota: int = 5

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
        if value != "mock":
            raise ValueError(
                f"unsupported AI_PROVIDER '{value}'; only 'mock' is implemented"
            )
        return value

    @field_validator(
        "max_upload_mb",
        "max_document_pages",
        "max_output_tokens",
        "request_timeout_seconds",
        "session_ttl_minutes",
        "session_max_per_user",
        "auth_rate_limit",
        "auth_ip_rate_limit",
        "auth_rate_window_seconds",
        "default_project_quota",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit must be positive")
        return value

    @field_validator("session_cookie_same_site")
    @classmethod
    def _same_site(cls, value: str) -> str:
        allowed = {"lax", "strict", "none"}
        if value.lower() not in allowed:
            raise ValueError(
                f"SESSION_COOKIE_SAME_SITE must be one of {sorted(allowed)}"
            )
        return value.lower()

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _trusted_proxies(cls, value: str) -> str:
        for entry in (item.strip() for item in value.split(",") if item.strip()):
            ipaddress.ip_network(entry, strict=False)
        return value

    @property
    def effective_session_cookie_name(self) -> str:
        """Use RFC 6265bis `__Host-` prefix automatically in HTTPS production."""
        if self.app_env == "production" and not self.session_cookie_name.startswith("__Host-"):
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    @property
    def effective_csrf_cookie_name(self) -> str:
        """Use `__Host-` cookie prefix in production to block domain cookie tossing."""
        if self.app_env == "production":
            return "__Host-mafhum_csrf"
        return "mafhum_csrf"

    def parsed_trusted_proxies(
        self,
    ) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [
            ipaddress.ip_network(item.strip(), strict=False)
            for item in self.trusted_proxy_ips.split(",")
            if item.strip()
        ]

    def parsed_allowed_origins(self) -> set[str]:
        return {
            origin.strip().rstrip("/").lower()
            for origin in self.allow_dev_origins.split(",")
            if origin.strip()
        }

    def validate_for_production(self) -> None:
        """Called at startup. Production must not run with insecure defaults."""
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.session_secret in ("change-me-in-production", ""):
            problems.append("SESSION_SECRET must be set in production")
        elif len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be at least 32 characters in production")
        if not self.database_url:
            problems.append("DATABASE_URL must be set in production")
        elif self.database_url.startswith("sqlite"):
            problems.append("SQLite DATABASE_URL is not supported in multi-user production")
        if self.session_cookie_same_site == "none":
            problems.append("SESSION_COOKIE_SAME_SITE must not be 'none' in production")
        if "*" in self.parsed_allowed_origins():
            problems.append("Wildcard '*' origin is forbidden with credentialed cookies")
        if problems:
            raise ValueError(
                "Insecure production configuration: " + "; ".join(problems)
            )


settings = Settings()
