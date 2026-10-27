"""Authentication endpoints: register, login, logout, session and CSRF token."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from mafhum_api.api.deps import Context, CurrentUser, DbSession
from mafhum_api.api.schemas import (
    CsrfTokenResponse,
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    UserResponse,
)
from mafhum_api.core.security import generate_csrf_token
from mafhum_api.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _set_auth_cookies(response: Response, context: Context, token: str, csrf: str) -> None:
    settings = context.settings
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_ttl_minutes * 60,
    }
    response.set_cookie(settings.session_cookie_name, token, **common)  # type: ignore[arg-type]
    # The CSRF cookie is intentionally readable by script: it is only useful in
    # combination with the Origin allowlist enforced on mutations.
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_minutes * 60,
    )


def _clear_auth_cookies(response: Response, context: Context) -> None:
    settings = context.settings
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _user_payload(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        preferred_language=user.preferred_language,
    )


@router.post("/register", response_model=SessionResponse, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    context: Context,
    db: DbSession,
) -> SessionResponse:
    user = context.identity.register(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    issued = context.identity.issue_session(db, user, user_agent=request.headers.get("user-agent"))
    context.audit.record(
        db, action="auth_register", actor_id=user.id, target_type="user", target_id=str(user.id)
    )
    db.commit()
    csrf = generate_csrf_token()
    _set_auth_cookies(response, context, issued.token, csrf)
    return SessionResponse(user=_user_payload(user), expires_at=issued.expires_at)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    context: Context,
    db: DbSession,
) -> SessionResponse:
    key = _client_key(request)
    # Counted in the database, so the configured limit is the real limit across
    # every API process rather than per worker.
    context.login_throttle.check(key)
    try:
        user = context.identity.authenticate(db, email=payload.email, password=payload.password)
    except Exception:
        # Recorded on its own connection, not the request's: this session is about
        # to be discarded, and the attempts worth counting are the failed ones.
        context.login_throttle.record(key, succeeded=False)
        context.audit.record_detached(
            context.database,
            action="auth_login_failed",
            outcome="denied",
            target_type="session",
            details={"reason": "bad_credentials"},
        )
        raise
    context.login_throttle.record(key, succeeded=True)
    issued = context.identity.issue_session(db, user, user_agent=request.headers.get("user-agent"))
    context.audit.record(db, action="auth_login", actor_id=user.id, target_type="session")
    db.commit()
    csrf = generate_csrf_token()
    _set_auth_cookies(response, context, issued.token, csrf)
    return SessionResponse(user=_user_payload(user), expires_at=issued.expires_at)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    context: Context,
    db: DbSession,
) -> Response:
    token = request.cookies.get(context.settings.session_cookie_name)
    context.identity.revoke_session(db, token)
    context.audit.record(db, action="auth_logout", target_type="session")
    db.commit()
    # A 204 must carry no body. `JSONResponse(content=None)` renders `b"null"`,
    # which desynchronises HTTP keep-alive framing and makes the *next* request
    # on the same connection fail — so use a bare Response with an empty body.
    result = Response(status_code=204)
    _clear_auth_cookies(result, context)
    return result


@router.get("/session", response_model=CsrfTokenResponse)
def session_csrf(user: CurrentUser, response: Response, context: Context) -> CsrfTokenResponse:
    """Issue or rotate the CSRF token for the current session."""
    csrf = generate_csrf_token()
    response.set_cookie(
        context.settings.csrf_cookie_name,
        csrf,
        httponly=False,
        secure=context.settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=context.settings.session_ttl_minutes * 60,
    )
    return CsrfTokenResponse(csrf_token=csrf)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return _user_payload(user)
