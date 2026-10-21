"""Request dependencies: database, current session, CSRF, rate limits."""

import hmac
from collections.abc import Generator
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as SaSession

from app.config import settings
from app.models.user import User
from app.security.rate_limit import SlidingWindowLimiter
from app.services.auth import AuthService

_engine: Engine | None = None
_engine_url: str | None = None


def _enable_sqlite_fks(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine() -> Engine | None:
    """Lazily create (or refresh if DATABASE_URL changed) the SQLAlchemy engine.

    Returns None when DATABASE_URL is unset so readiness checks degrade cleanly.
    """
    global _engine, _engine_url
    if not settings.database_url:
        return None
    if _engine is None or _engine_url != settings.database_url:
        if _engine is not None:
            _engine.dispose()
        connect_args: dict[str, Any] = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if settings.database_url.startswith("sqlite"):
            event.listen(engine, "connect", _enable_sqlite_fks)
        _engine = engine
        _engine_url = settings.database_url
    return _engine


def get_db() -> Generator[SaSession | None, None, None]:
    """Yield a database session, or None when no database is configured."""
    engine = get_engine()
    if engine is None:
        yield None
        return
    db = SaSession(engine, autoflush=False, autocommit=False)
    try:
        yield db
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


# Rate limiters keyed per identifier for authentication endpoints.
register_limiter = SlidingWindowLimiter(
    settings.auth_rate_limit, settings.auth_rate_window_seconds
)
login_limiter = SlidingWindowLimiter(
    settings.auth_rate_limit, settings.auth_rate_window_seconds
)


def current_user(
    request: Request, db: SaSession | None = Depends(get_db)
) -> User | None:
    """Resolve the authenticated user strictly from server-side session state.

    Never reads client-supplied user IDs from headers, query params, or bodies.
    """
    if db is None:
        return None
    token = request.cookies.get(settings.session_cookie_name)
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


def require_csrf(request: Request) -> None:
    """Double-submit CSRF check for state-changing authenticated requests.

    Clients must send header `X-CSRF-Token` matching the `mafhum_csrf` cookie.
    Safe methods (`GET`, `HEAD`, `OPTIONS`) are exempt.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie_token = request.cookies.get("mafhum_csrf")
    header_token = request.headers.get("x-csrf-token")
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing",
        )
    if not hmac.compare_digest(cookie_token.encode("utf-8"), header_token.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


def set_auth_cookies(
    response: Response, session_token: str | None, csrf_token: str | None
) -> None:
    """Set or clear session (`HttpOnly`) and CSRF (`X-CSRF-Token`) cookies."""
    secure = settings.app_env == "production" or settings.session_cookie_same_site == "none"
    same_site = settings.session_cookie_same_site
    max_age_session = settings.session_ttl_minutes * 60 if session_token else 0
    max_age_csrf = settings.session_ttl_minutes * 60 if csrf_token else 0

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token or "",
        httponly=True,
        secure=secure,
        samesite=same_site,  # type: ignore[arg-type]
        path="/",
        max_age=max_age_session,
        expires=0 if not session_token else None,
    )
    response.set_cookie(
        key="mafhum_csrf",
        value=csrf_token or "",
        httponly=False,
        secure=secure,
        samesite=same_site,  # type: ignore[arg-type]
        path="/",
        max_age=max_age_csrf,
        expires=0 if not csrf_token else None,
    )


def check_register_rate(identifier: str) -> None:
    register_limiter.check(
        f"register:{identifier}",
        max_attempts=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    )


def check_login_rate(client_id: str, email: str) -> None:
    # Enforce per-account AND broader per-IP limits against brute force & spraying.
    login_limiter.check(
        f"login:ip:{client_id}",
        max_attempts=settings.auth_rate_limit * 2,
        window_seconds=settings.auth_rate_window_seconds,
    )
    login_limiter.check(
        f"login:acct:{client_id}:{email}",
        max_attempts=settings.auth_rate_limit,
        window_seconds=settings.auth_rate_window_seconds,
    )
