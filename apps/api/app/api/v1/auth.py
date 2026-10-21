"""Authentication & session routes: register, login, logout, sessions, CSRF."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as SaSession

from app.api.deps import (
    check_login_rate,
    check_register_rate,
    require_csrf,
    require_db,
    require_user,
    set_auth_cookies,
)
from app.config import settings
from app.models.user import User
from app.schemas.auth import (
    AuthMessage,
    SessionListOut,
    SessionOut,
    UserLogin,
    UserOut,
    UserRegister,
)
from app.security.session import hash_token, new_csrf_token
from app.services.auth import AuthError, AuthService

router = APIRouter(tags=["auth"])


def _client_identifier(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    request: Request,
    response: Response,
    payload: UserRegister,
    db: SaSession = Depends(require_db),
) -> UserOut:
    check_register_rate(_client_identifier(request))
    service = AuthService(db)
    try:
        user = service.register(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            preferred_locale=payload.preferred_locale,
        )
    except AuthError as exc:
        if str(exc) == "email_already_registered":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Registration could not be completed for the provided email",
            ) from None
        raise
    session_token, csrf_token = service.create_session(user)
    db.commit()
    db.refresh(user)
    set_auth_cookies(response, session_token, csrf_token)
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=UserOut)
def login(
    request: Request,
    response: Response,
    payload: UserLogin,
    db: SaSession = Depends(require_db),
) -> UserOut:
    check_login_rate(_client_identifier(request), payload.email)
    service = AuthService(db)
    user = service.authenticate(payload.email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    session_token, csrf_token = service.create_session(user)
    db.commit()
    db.refresh(user)
    set_auth_cookies(response, session_token, csrf_token)
    return UserOut.model_validate(user)


@router.post("/auth/logout", response_model=AuthMessage)
def logout(
    request: Request,
    response: Response,
    _user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> AuthMessage:
    require_csrf(request)
    service = AuthService(db)
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        service.revoke_session(token)
        db.commit()
    set_auth_cookies(response, None, None)
    return AuthMessage(detail="Logged out")


@router.post("/auth/logout-all", response_model=AuthMessage)
def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> AuthMessage:
    require_csrf(request)
    service = AuthService(db)
    service.revoke_all_user_sessions(user.id)
    db.commit()
    set_auth_cookies(response, None, None)
    return AuthMessage(detail="All sessions revoked")


@router.get("/auth/sessions", response_model=SessionListOut)
def list_sessions(
    request: Request,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> SessionListOut:
    service = AuthService(db)
    token = request.cookies.get(settings.session_cookie_name)
    current_digest = hash_token(token) if token else ""
    active = service.list_active_sessions(user.id)
    return SessionListOut(
        items=[
            SessionOut(
                id=UUID(s.id),
                created_at=s.created_at,
                expires_at=s.expires_at,
                last_used_at=s.last_used_at,
                is_current=(s.token_hash == current_digest),
            )
            for s in active
        ]
    )


@router.delete("/auth/sessions/{session_id}", response_model=AuthMessage)
def revoke_session_by_id(
    session_id: str,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> AuthMessage:
    require_csrf(request)
    service = AuthService(db)
    # Check if revoking own current session so cookies can be cleared cleanly too.
    token = request.cookies.get(settings.session_cookie_name)
    current_sess = service.get_session(token)
    revoked = service.revoke_session_by_id(user.id, session_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    db.commit()
    if current_sess is not None and current_sess.id == session_id:
        set_auth_cookies(response, None, None)
    return AuthMessage(detail="Session revoked")


@router.get("/auth/me", response_model=UserOut)
def auth_me(user: User = Depends(require_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/auth/csrf-token", response_model=AuthMessage)
def csrf_token_endpoint(response: Response) -> AuthMessage:
    token = new_csrf_token()
    secure = settings.app_env == "production" or settings.session_cookie_same_site == "none"
    response.set_cookie(
        key="mafhum_csrf",
        value=token,
        httponly=False,
        secure=secure,
        samesite=settings.session_cookie_same_site,  # type: ignore[arg-type]
        path="/",
        max_age=settings.session_ttl_minutes * 60,
    )
    return AuthMessage(detail="CSRF cookie set")
