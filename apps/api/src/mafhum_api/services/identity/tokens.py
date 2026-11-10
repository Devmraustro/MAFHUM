"""Single-use tokens for password reset and address verification.

Properties that matter, and why each one is here
------------------------------------------------
* **Never stored in the clear.** The database keeps a SHA-256 digest. A dump of
  ``user_tokens`` therefore yields nothing that can redeem a reset.
* **Hash-only lookup.** The token is the lookup key, so an unknown token and a
  consumed one are both "not found". To still give the user an honest message,
  the first 8 characters (``token_prefix``) are stored separately: they identify
  an already-consumed or expired token without being usable as one.
* **Single use.** Redemption sets ``consumed_at`` inside the request transaction
  and takes a row lock, so two simultaneous submissions of the same link cannot
  both succeed.
* **Short-lived, and superseded on issue.** Issuing a new token for a purpose
  invalidates every live token of that purpose for that user. Only the newest
  link works, which is the behaviour users expect from "resend".
* **Bounded.** Expired rows are deleted by the same pass that prunes expired
  sessions.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from mafhum_api.core.errors import ValidationError
from mafhum_api.db.base import execute_rowcount
from mafhum_api.db.models import TokenPurpose, User, UserToken

logger = logging.getLogger(__name__)

# 32 random bytes -> 43 url-safe characters.
TOKEN_BYTES = 32
PREFIX_LENGTH = 8


class TokenRejectedError(ValidationError):
    """A presented token cannot be redeemed."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = "invalid_token"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: datetime


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountTokenService:
    def __init__(self, *, reset_ttl_minutes: int, verification_ttl_minutes: int) -> None:
        self._reset_ttl = timedelta(minutes=reset_ttl_minutes)
        self._verify_ttl = timedelta(minutes=verification_ttl_minutes)

    def _ttl_for(self, purpose: str) -> timedelta:
        return self._reset_ttl if purpose == TokenPurpose.PASSWORD_RESET.value else self._verify_ttl

    def issue(self, db: Session, *, user: User, purpose: str) -> IssuedToken:
        """Issue a fresh token, invalidating any live token of the same purpose."""
        now = datetime.now(UTC)
        db.execute(
            update(UserToken)
            .where(
                UserToken.user_id == user.id,
                UserToken.purpose == purpose,
                UserToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._ttl_for(purpose)
        db.add(
            UserToken(
                id=uuid.uuid4(),
                user_id=user.id,
                purpose=purpose,
                token_hash=_digest(token),
                token_prefix=token[:PREFIX_LENGTH],
                expires_at=expires_at,
            )
        )
        db.flush()
        return IssuedToken(token=token, expires_at=expires_at)

    def redeem(self, db: Session, *, token: str, purpose: str) -> User:
        """Consume ``token`` and return its owner, or raise ``TokenRejectedError``.

        The three rejection reasons are distinct on purpose: "expired" and
        "already used" are actionable for the user ("request a new link"),
        whereas "invalid" is not.
        """
        if not token or len(token) < PREFIX_LENGTH or len(token) > 256:
            raise TokenRejectedError("invalid", "رابط غير صالح.")
        row = db.scalar(
            select(UserToken)
            .where(UserToken.token_hash == _digest(token), UserToken.purpose == purpose)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if row is None:
            # Fall back to the prefix so the message can be accurate: a stored
            # prefix with no live row means the link was already redeemed.
            known = db.scalar(
                select(UserToken.id).where(
                    UserToken.token_prefix == token[:PREFIX_LENGTH],
                    UserToken.purpose == purpose,
                )
            )
            if known is not None:
                raise TokenRejectedError("used", "هذا الرابط مستعمل من قبل. اطلب رابطًا جديدًا.")
            raise TokenRejectedError("invalid", "رابط غير صالح.")
        if row.consumed_at is not None:
            raise TokenRejectedError("used", "هذا الرابط مستعمل من قبل. اطلب رابطًا جديدًا.")
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise TokenRejectedError("expired", "انتهت صلاحية هذا الرابط. اطلب رابطًا جديدًا.")
        user = db.get(User, row.user_id)
        if user is None:
            raise TokenRejectedError("invalid", "رابط غير صالح.")
        row.consumed_at = now
        db.flush()
        return user

    def purge_expired(self, db: Session) -> int:
        now = datetime.now(UTC)
        return execute_rowcount(db, delete(UserToken).where(UserToken.expires_at < now))
