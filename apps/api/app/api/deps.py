"""Request dependencies: database, sessions, CSRF, origin checks, and rate limits."""

from collections.abc import Generator
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as SaSession

from app.config import settings
from app.models.user import User
from app.security.network import is_https_request, resolve_client_ip, verify_request_origin
from app.security.rate_limit import SlidingWindowLimiter, account_limiter_key
from app.security.session import verify_csrf_token
from app.services.auth import AuthService

_engine: Engine | None = None
_engine_url: str | None = None


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def get_engine() -> Engine | None:
    """Lazily create (or refresh if DATABASE_URL changed) the SQLAlchemy engine."""
    global _engine, _engine_url
    if not settings.database_url:
        return None
    if _engine is None or _engine_url != settings.database_url:
        if _engine is not None:
            _engine.dispose()
        if settings.database_url.startswith("sqlite"):
            engine = create_engine(
                settings.database_url,
                pool_pre_ping=True,
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )
            event.listen(engine, "connect", _configure_sqlite_connection)
        else:
            engine = create_engine(
                settings.database_url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
            )
        _engine = engine
        _engine_url = settings.database_url
    return _engine


def get_db() -> Generator[SaSession | None, None, None]:
    """Yield a database session with guaranteed rollback on unhandled errors."""
    engine = get_engine()
    if engine is None:
        yield None
        return
    db = SaSession(engine, autoflush=False, autocommit=False)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def require_db(db: SaSession | None = Depends(get_db)) -> SaSession:
    """Database dependency that raises 503 when no database is configured."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )
    return db


# Independent rate limiters:
# - `register_limiter`: keyed per resolved client IP (`ip:{ip}`)
# - `login_account_limiter`: keyed per HMAC-SHA256 digest of normalized email
#   (completely independent of client IP; never stores plaintext email PII)
# - `login_ip_limiter`: keyed per resolved client IP (`ip:{ip}`)
register_limiter = SlidingWindowLimiter(
    settings.auth_rate_limit, settings.auth_rate_window_seconds
)
login_account_limiter = SlidingWindowLimiter(
    settings.auth_rate_limit, settings.auth_rate_window_seconds
)
login_ip_limiter = SlidingWindowLimiter(
    settings.auth_ip_rate_limit, settings.auth_rate_window_seconds
)
# Backward-compatible alias pointing at the account limiter for existing imports.
login_limiter = login_account_limiter


def reset_all_limiters() -> None:
    register_limiter.reset()
    login_account_limiter.reset()
    login_ip_limiter.reset()


def client_ip_from_request(request: Request) -> str:
    return resolve_client_ip(request, settings.parsed_trusted_proxies())


def current_user(
    request: Request, db: SaSession | None = Depends(get_db)
) -> User | None:
    """Resolve the authenticated user strictly from server-side session state.

    Never reads client-supplied user IDs from headers, query params, or bodies.
    """
    if db is None:
        return None
    token = request.cookies.get(settings.effective_session_cookie_name)
    if not token:
        return None
    service = AuthService(db)
    user = service.get_session_user(token)
    if user is not None:
        db.commit()
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    """Dependency for protected routes: 401 when no valid session is active."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def require_same_origin(request: Request) -> None:
    """Validate `Origin` / `Referer` on state-changing requests."""
    verify_request_origin(
        request,
        settings.parsed_allowed_origins(),
        settings.parsed_trusted_proxies(),
    )


def require_csrf(request: Request) -> None:
    """Enforce origin check + session-bound HMAC double-submit CSRF check."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    require_same_origin(request)
    cookie_token = request.cookies.get(settings.effective_csrf_cookie_name)
    header_token = request.headers.get("x-csrf-token")
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing",
        )
    session_token = request.cookies.get(settings.effective_session_cookie_name, "")
    if not verify_csrf_token(cookie_token, header_token, session_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


def set_auth_cookies(
    request: Request,
    response: Response,
    session_token: str | None,
    csrf_token: str | None,
) -> None:
    """Set or clear session (`HttpOnly`) and session-bound CSRF cookies."""
    https = is_https_request(request, settings.parsed_trusted_proxies())
    secure = (
        settings.app_env == "production"
        or settings.session_cookie_same_site == "none"
        or https
    )
    same_site = settings.session_cookie_same_site
    max_age_session = settings.session_ttl_minutes * 60 if session_token else 0
    max_age_csrf = settings.session_ttl_minutes * 60 if csrf_token else 0

    response.set_cookie(
        key=settings.effective_session_cookie_name,
        value=session_token or "",
        httponly=True,
        secure=secure,
        samesite=same_site,  # type: ignore[arg-type]
        path="/",
        max_age=max_age_session,
        expires=0 if not session_token else None,
    )
    response.set_cookie(
        key=settings.effective_csrf_cookie_name,
        value=csrf_token or "",
        httponly=False,
        secure=secure,
        samesite=same_site,  # type: ignore[arg-type]
        path="/",
        max_age=max_age_csrf,
        expires=0 if not csrf_token else None,
    )


def check_register_rate(request: Request) -> None:
    client_ip = client_ip_from_request(request)
    register_limiter.check(
        f"ip:{client_ip}",
        max_attempts=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    )


def check_login_rate(request: Request, email: str) -> None:
    """Enforce BOTH an IP-independent account limit AND an account-independent IP limit."""
    client_ip = client_ip_from_request(request)
    acct_key = account_limiter_key(email, settings.session_secret)

    # 1. Per-IP limit (independent of target email — stops password spraying).
    login_ip_limiter.check(
        f"ip:{client_ip}",
        max_attempts=settings.auth_ip_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    )
    # 2. Per-account limit (completely independent of client IP — stops IP-rotated brute force).
    login_account_limiter.check(
        acct_key,
        max_attempts=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    )
