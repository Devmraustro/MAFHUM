"""Authentication, session management, profile, export, and deletion service."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as SaSession

from app.config import settings
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.user import User
from app.security.password import hash_password, verify_dummy_password, verify_password
from app.security.session import hash_token, new_csrf_token, new_session_token

RETENTION_NOTICE = (
    "Live account credentials, server sessions, and owned project records have been "
    "deleted from the primary database. Encrypted database backups and redacted "
    "operational logs are subject to infrastructure retention windows and are not "
    "retroactively rewritten; no third-party data deletion is claimed unless verified."
)


class AuthError(Exception):
    """Raised when an authentication or lifecycle precondition fails."""


class AuthService:
    def __init__(self, db: SaSession) -> None:
        self.db = db

    # ----- registration -----
    def register(
        self,
        email: str,
        password: str,
        full_name: str | None = None,
        preferred_locale: str = "ar",
    ) -> User:
        normalized_email = email.strip().lower()
        existing = self.db.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()
        if existing is not None:
            raise AuthError("email_already_registered")
        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            full_name=full_name,
            preferred_locale=preferred_locale,
            project_quota=settings.default_project_quota,
            projects_count=0,
        )
        self.db.add(user)
        self.db.flush()
        return user

    # ----- login / sessions -----
    def authenticate(self, email: str, password: str) -> User | None:
        """Return the user on success, or None on invalid credentials.

        Runs dummy bcrypt verification when the email does not exist so response
        timing does not leak whether an email address is registered.
        """
        normalized_email = email.strip().lower()
        user = self.db.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()
        if user is None:
            verify_dummy_password(password)
            return None
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        return user

    def create_session(self, user: User) -> tuple[str, str]:
        """Create a server-side session. Returns (session_token, csrf_token)."""
        active_sessions = list(
            self.db.execute(
                select(SessionModel)
                .where(SessionModel.user_id == user.id)
                .where(SessionModel.revoked.is_(False))
                .order_by(SessionModel.created_at.asc())
            ).scalars().all()
        )
        while len(active_sessions) >= settings.session_max_per_user:
            oldest = active_sessions.pop(0)
            oldest.revoked = True

        token = new_session_token()
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.session_ttl_minutes
        )
        session = SessionModel(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            last_used_at=datetime.now(UTC),
        )
        self.db.add(session)
        self.db.flush()
        return token, new_csrf_token()

    def get_session(self, token: str | None) -> SessionModel | None:
        if not token:
            return None
        digest = hash_token(token)
        sess = self.db.execute(
            select(SessionModel).where(SessionModel.token_hash == digest)
        ).scalar_one_or_none()
        if sess is None or sess.revoked or sess.is_expired:
            return None
        return sess

    def get_session_user(self, token: str | None) -> User | None:
        sess = self.get_session(token)
        if sess is None:
            return None
        user = self.db.get(User, sess.user_id)
        if user is None or not user.is_active:
            return None
        sess.last_used_at = datetime.now(UTC)
        self.db.flush()
        return user

    def list_active_sessions(self, user_id: str) -> list[SessionModel]:
        sessions = list(
            self.db.execute(
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .where(SessionModel.revoked.is_(False))
                .order_by(SessionModel.created_at.desc())
            ).scalars().all()
        )
        return [s for s in sessions if not s.is_expired]

    def revoke_session(self, token: str) -> bool:
        digest = hash_token(token)
        sess = self.db.execute(
            select(SessionModel).where(SessionModel.token_hash == digest)
        ).scalar_one_or_none()
        if sess is None or sess.revoked:
            return False
        sess.revoked = True
        self.db.flush()
        return True

    def revoke_session_by_id(self, user_id: str, session_id: str) -> bool:
        sess = self.db.execute(
            select(SessionModel)
            .where(SessionModel.id == session_id)
            .where(SessionModel.user_id == user_id)
        ).scalar_one_or_none()
        if sess is None:
            return False
        sess.revoked = True
        self.db.flush()
        return True

    def revoke_all_user_sessions(self, user_id: str) -> int:
        result = self.db.execute(
            update(SessionModel)
            .where(SessionModel.user_id == user_id)
            .where(SessionModel.revoked.is_(False))
            .values(revoked=True)
        )
        self.db.flush()
        return int(result.rowcount or 0)

    # ----- profile -----
    def update_profile(
        self,
        user: User,
        *,
        full_name: str | None,
        update_full_name: bool,
        preferred_locale: str | None,
        update_locale: bool,
    ) -> User:
        if update_full_name:
            user.full_name = full_name
        if update_locale and preferred_locale is not None:
            user.preferred_locale = preferred_locale
        user.updated_at = datetime.now(UTC)
        self.db.flush()
        return user

    # ----- account deletion -----
    def delete_account(self, user_id: str) -> tuple[int, int]:
        """Atomically revoke & delete all sessions, owned projects, and user record.

        Returns `(sessions_removed, projects_deleted)`.
        """
        user = self.db.get(User, user_id)
        if user is None:
            raise AuthError("user_not_found")

        sessions_count = int(
            self.db.execute(
                select(func.count(SessionModel.id)).where(
                    SessionModel.user_id == user_id
                )
            ).scalar_one()
        )
        projects_count = int(
            self.db.execute(
                select(func.count(Project.id)).where(Project.owner_id == user_id)
            ).scalar_one()
        )

        # Explicitly delete child records in addition to ON DELETE CASCADE so
        # deletion lifecycle guarantees hold regardless of DB backend settings.
        self.db.execute(delete(SessionModel).where(SessionModel.user_id == user_id))
        self.db.execute(delete(Project).where(Project.owner_id == user_id))
        self.db.delete(user)
        self.db.flush()
        return sessions_count, projects_count
