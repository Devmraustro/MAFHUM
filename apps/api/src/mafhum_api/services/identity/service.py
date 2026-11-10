"""Identity: registration, session issue/verify/revoke, account deletion hook."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mafhum_api.core.errors import (
    AuthenticationError,
    ConflictError,
    ValidationError,
)
from mafhum_api.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from mafhum_api.db.base import execute_rowcount
from mafhum_api.db.models import DeletionState, User, UserSession

logger = logging.getLogger(__name__)

EMAIL_MAX = 320
DISPLAY_NAME_MAX = 120


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    preferred_language: str


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    expires_at: datetime


class IdentityService:
    def __init__(self, *, session_ttl_minutes: int) -> None:
        self._ttl = timedelta(minutes=session_ttl_minutes)

    # --- registration --------------------------------------------------------
    def register(self, db: Session, *, email: str, password: str, display_name: str) -> User:
        normalized = _normalize_email(email)
        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            raise ValidationError(
                "كلمة المرور قصيرة جدًا.",
                field_errors={"password": ["يجب أن تكون 8 أحرف على الأقل."]},
            ) from exc
        user = User(
            email=normalized,
            display_name=display_name.strip()[:DISPLAY_NAME_MAX],
            password_hash=password_hash,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError(
                "هذا البريد الإلكتروني مستعمل بالفعل.",
                details={"field": "email"},
            ) from exc
        logger.info("user registered", extra={"event": "user_registered", "user_id": str(user.id)})
        return user

    def authenticate(self, db: Session, *, email: str, password: str) -> User:
        normalized = _normalize_email(email)
        user = db.scalar(select(User).where(User.email == normalized))
        if user is None:
            # Identical message for unknown email and wrong password: the login
            # form must not act as an account enumerator.
            raise AuthenticationError("بيانات الدخول غير صحيحة.")
        if user.deletion_state != DeletionState.ACTIVE.value:
            raise AuthenticationError("بيانات الدخول غير صحيحة.")
        if not verify_password(password, user.password_hash):
            logger.info("failed login", extra={"event": "login_failed"})
            raise AuthenticationError("بيانات الدخول غير صحيحة.")
        return user

    def find_active_by_email(self, db: Session, email: str) -> User | None:
        """Look up a live account by address, or ``None``.

        Used only by the reset flow, which must answer identically for a
        registered and an unknown address. Returns nothing for an account that
        is pending or completed deletion: an address that is being erased must
        not be recoverable through this path.
        """
        normalized = _normalize_email(email)
        user = db.scalar(select(User).where(User.email == normalized))
        if user is None or user.deletion_state != DeletionState.ACTIVE.value:
            return None
        return user

    # --- sessions ------------------------------------------------------------
    def issue_session(
        self, db: Session, user: User, *, user_agent: str | None = None
    ) -> IssuedSession:
        token = generate_session_token()
        expires_at = datetime.now(UTC) + self._ttl
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_session_token(token),
                expires_at=expires_at,
                user_agent=(user_agent or "")[:256] or None,
            )
        )
        db.flush()
        return IssuedSession(token=token, expires_at=expires_at)

    def resolve_session(self, db: Session, token: str | None) -> User:
        if not token:
            raise AuthenticationError()
        token_hash = hash_session_token(token)
        session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
        now = datetime.now(UTC)
        if session is None or session.revoked_at is not None:
            raise AuthenticationError()
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise AuthenticationError()
        user = db.get(User, session.user_id)
        if user is None or user.deletion_state != DeletionState.ACTIVE.value:
            raise AuthenticationError()
        return user

    def revoke_session(self, db: Session, token: str | None) -> None:
        if not token:
            return
        token_hash = hash_session_token(token)
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == token_hash)
            .values(revoked_at=datetime.now(UTC))
        )

    def revoke_all_sessions(self, db: Session, user_id: uuid.UUID) -> int:
        return execute_rowcount(
            db,
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC)),
        )

    def revoke_sessions_except(
        self, db: Session, user_id: uuid.UUID, keep_token_hash: str | None
    ) -> int:
        """Revoke every live session except the one identified by its digest.

        ``keep_token_hash`` is the digest of the caller's own session token, so
        the device performing the password change stays signed in. When it is
        absent (a reset, which happens without a session) every session ends.
        """
        stmt = update(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        if keep_token_hash:
            stmt = stmt.where(UserSession.token_hash != keep_token_hash)
        return execute_rowcount(db, stmt.values(revoked_at=datetime.now(UTC)))

    def purge_expired_sessions(self, db: Session) -> int:
        now = datetime.now(UTC)
        return execute_rowcount(db, delete(UserSession).where(UserSession.expires_at < now))

    # --- account lifecycle ---------------------------------------------------
    def change_password(
        self,
        db: Session,
        user: User,
        *,
        current_password: str,
        new_password: str,
        keep_token_hash: str | None = None,
    ) -> int:
        """Change the password and revoke every *other* session.

        The current session survives (a user who changes their password on a
        borrowed laptop is not thrown out of it here), but every other session
        is revoked: a password change is the moment a user expects an intruder
        to be logged out. Returns the number of sessions revoked.
        """
        if not verify_password(current_password, user.password_hash):
            raise ValidationError(
                "كلمة المرور الحالية غير صحيحة.",
                field_errors={"current_password": ["غير صحيحة."]},
            )
        try:
            user.password_hash = hash_password(new_password)
        except ValueError as exc:
            raise ValidationError(
                "كلمة المرور قصيرة جدًا.",
                field_errors={"new_password": ["يجب أن تكون 8 أحرف على الأقل."]},
            ) from exc
        db.flush()
        revoked = self.revoke_sessions_except(db, user.id, keep_token_hash)
        logger.info(
            "password changed", extra={"event": "password_changed", "user_id": str(user.id)}
        )
        return revoked

    def set_password_from_reset(self, db: Session, user: User, *, new_password: str) -> None:
        """Set a password from a redeemed reset token, revoking every session.

        No current password is required (the token proved control of the inbox),
        and every session is revoked — including the caller's — because the
        reason a user resets a password is usually that they lost control of it.
        """
        try:
            user.password_hash = hash_password(new_password)
        except ValueError as exc:
            raise ValidationError(
                "كلمة المرور قصيرة جدًا.",
                field_errors={"password": ["يجب أن تكون 8 أحرف على الأقل."]},
            ) from exc
        db.flush()
        self.revoke_all_sessions(db, user.id)
        logger.info(
            "password reset completed",
            extra={"event": "password_reset_completed", "user_id": str(user.id)},
        )

    def email_is_verified(self, db: Session, user_id: uuid.UUID) -> bool:
        """Whether this address was ever proven, derived from token history.

        A consumed ``email_verification`` token is the evidence. There is no
        mutable boolean to drift out of sync with that evidence, and an address
        that changed would start unverified again (a new token must be redeemed).

        The state is advisory: it gates no read path. It is shown to the user and
        used to nudge them to confirm, never to deny access to their own data.
        """
        from mafhum_api.db.models import TokenPurpose, UserToken

        row = db.scalar(
            select(UserToken.id)
            .where(
                UserToken.user_id == user_id,
                UserToken.purpose == TokenPurpose.EMAIL_VERIFICATION.value,
                UserToken.consumed_at.is_not(None),
            )
            .limit(1)
        )
        return row is not None

    def list_sessions(self, db: Session, user_id: uuid.UUID) -> list[UserSession]:
        """Every live session, newest first. Expired and revoked rows excluded."""
        now = datetime.now(UTC)
        return list(
            db.scalars(
                select(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                )
                .order_by(UserSession.created_at.desc())
            )
        )

    def find_session(
        self, db: Session, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> UserSession | None:
        session = db.get(UserSession, session_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    def revoke_session_by_id(self, db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        session = self.find_session(db, user_id, session_id)
        if session is None:
            return False
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            db.flush()
        return True

    def describe(self, user: User) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            preferred_language=user.preferred_language,
        )


def _normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise ValidationError("البريد الإلكتروني غير صالح.", field_errors={"email": ["غير صالح."]})
    normalized = email.strip().lower()
    if not normalized or len(normalized) > EMAIL_MAX or "@" not in normalized:
        raise ValidationError("البريد الإلكتروني غير صالح.", field_errors={"email": ["غير صالح."]})
    return normalized
