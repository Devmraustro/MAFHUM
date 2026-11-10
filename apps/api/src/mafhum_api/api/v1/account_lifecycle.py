"""Account lifecycle endpoints: password reset, verification, sessions.

Security decisions that shaped this module
------------------------------------------
* **No account enumeration on reset.** ``POST /auth/password-reset/request``
  returns the same body and status for a registered and an unregistered address.
  The only asymmetry is that the mail is queued for one of them, which the caller
  cannot observe.
* **The link is built from configuration, never from the request.** ``Host`` and
  ``X-Forwarded-Host`` are attacker-controlled; a reset link built from them
  would let an attacker receive the victim's token. ``PUBLIC_WEB_URL`` is the
  single source of the origin.
* **Redeeming a reset revokes every session.** The usual reason to reset a
  password is that the account is no longer exclusively yours.
* **Changing a password requires the current one**, and revokes every *other*
  session — the caller stays signed in on the device they are using.
* **Honest unavailability.** When no mail transport is configured the reset
  request answers ``503`` with a message that says so, rather than a cheerful
  ``200`` for a message nobody will ever receive.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus

from fastapi import APIRouter, Request, Response, status

from mafhum_api.api.deps import (
    Context,
    CsrfGuard,
    CsrfGuardAnonymous,
    CurrentUser,
    DbSession,
)
from mafhum_api.api.schemas import (
    AccountStateResponse,
    EmailVerificationConfirmRequest,
    MessageResponse,
    PasswordChangeRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    SessionItemResponse,
    SessionListResponse,
)
from mafhum_api.core.errors import DomainError, NotFoundError
from mafhum_api.db.models import TokenPurpose
from mafhum_api.services.notifications.email import (
    TEMPLATE_EMAIL_VERIFICATION,
    TEMPLATE_PASSWORD_RESET,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_delivery(context: Context) -> None:
    """Fail honestly when this deployment cannot send mail."""
    if not context.settings.email_delivery_configured:
        raise EmailDeliveryUnavailableError()


class EmailDeliveryUnavailableError(DomainError):
    """Mail cannot be delivered in this deployment."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "email_delivery_unavailable"

    def __init__(self) -> None:
        super().__init__("إرسال البريد غير مهيّأ في هذه النسخة. تواصل مع مسؤول المنصة.")


def _reset_link(context: Context, token: str) -> str:
    return f"{context.settings.public_web_url}/reset-password?token={token}"


def _verify_link(context: Context, token: str) -> str:
    return f"{context.settings.public_web_url}/verify-email?token={token}"


@router.post(
    "/password-reset/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[CsrfGuardAnonymous],
)
def request_password_reset(
    payload: PasswordResetRequest,
    context: Context,
    db: DbSession,
) -> MessageResponse:
    """Queue a reset mail, answering identically whether or not the address exists."""
    _require_delivery(context)

    user = context.identity.find_active_by_email(db, payload.email)
    if user is not None:
        issued = context.account_tokens.issue(
            db, user=user, purpose=TokenPurpose.PASSWORD_RESET.value
        )
        context.outbox.enqueue(
            db,
            recipient=user.email,
            template=TEMPLATE_PASSWORD_RESET,
            user_id=user.id,
            payload={
                "link": _reset_link(context, issued.token),
                "ttl_minutes": context.settings.password_reset_ttl_minutes,
            },
        )
        context.audit.record(
            db,
            action="password_reset_requested",
            actor_id=user.id,
            target_type="user",
            target_id=str(user.id),
        )
    else:
        # Recorded without an actor: an audit trail of "someone asked to reset an
        # address we do not know" is useful for abuse detection and reveals
        # nothing about the account because there is none.
        context.audit.record(
            db,
            action="password_reset_requested_unknown",
            outcome="denied",
            target_type="user",
            details={"reason": "no_such_account"},
        )
    # Deliver inline: this endpoint is rare, the transaction is tiny, and a
    # queued-but-undelivered reset is worthless to the user waiting for it. A
    # delivery failure is reported, never smoothed over.
    report = context.outbox.deliver_pending(db)
    db.commit()
    if report.sent == 0 and report.failed > 0:
        raise EmailDeliveryUnavailableError()
    return MessageResponse(
        message="إذا كان البريد مسجّلًا لدينا فستصلك رسالة فيها رابط إعادة التعيين.",
    )


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    dependencies=[CsrfGuardAnonymous],
)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    context: Context,
    db: DbSession,
) -> MessageResponse:
    user = context.account_tokens.redeem(
        db, token=payload.token, purpose=TokenPurpose.PASSWORD_RESET.value
    )
    context.identity.set_password_from_reset(db, user, new_password=payload.password)
    context.audit.record(
        db,
        action="password_reset_completed",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    db.commit()
    return MessageResponse(
        message="تم تعيين كلمة المرور. سجّل الدخول من جديد بكلمة المرور الجديدة.",
    )


@router.post(
    "/email-verification/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[CsrfGuard],
)
def request_email_verification(
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> MessageResponse:
    _require_delivery(context)
    if context.identity.email_is_verified(db, user.id):
        return MessageResponse(message="بريدك مؤكَّد بالفعل.")

    issued = context.account_tokens.issue(
        db, user=user, purpose=TokenPurpose.EMAIL_VERIFICATION.value
    )
    context.outbox.enqueue(
        db,
        recipient=user.email,
        template=TEMPLATE_EMAIL_VERIFICATION,
        user_id=user.id,
        payload={
            "link": _verify_link(context, issued.token),
            "ttl_minutes": context.settings.email_verification_ttl_minutes,
        },
    )
    context.audit.record(
        db,
        action="email_verification_requested",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    report = context.outbox.deliver_pending(db)
    db.commit()
    if report.sent == 0 and report.failed > 0:
        raise EmailDeliveryUnavailableError()
    return MessageResponse(message="أرسلنا رابط التأكيد إلى بريدك.")


@router.post(
    "/email-verification/confirm",
    response_model=MessageResponse,
    dependencies=[CsrfGuardAnonymous],
)
def confirm_email_verification(
    payload: EmailVerificationConfirmRequest,
    context: Context,
    db: DbSession,
) -> MessageResponse:
    user = context.account_tokens.redeem(
        db, token=payload.token, purpose=TokenPurpose.EMAIL_VERIFICATION.value
    )
    # The consumed token IS the verification evidence; email_is_verified()
    # derives the state from it, so nothing mutable is written here.
    context.audit.record(
        db,
        action="email_verified",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    db.commit()
    return MessageResponse(message="تم تأكيد بريدك الإلكتروني.")


@router.post(
    "/password/change",
    response_model=MessageResponse,
    dependencies=[CsrfGuard],
)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> MessageResponse:
    from mafhum_api.core.security import hash_session_token

    current_digest = hash_session_token(
        request.cookies.get(context.settings.session_cookie_name, "")
    )
    revoked = context.identity.change_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        keep_token_hash=current_digest,
    )
    context.audit.record(
        db,
        action="password_changed",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        details={"sessions_revoked": revoked},
    )
    db.commit()
    return MessageResponse(
        message=f"تم تغيير كلمة المرور، وأُنهيت {revoked} جلسة أخرى على أجهزة أخرى.",
    )


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    request: Request,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> SessionListResponse:
    from mafhum_api.core.security import hash_session_token

    current_hash = hash_session_token(request.cookies.get(context.settings.session_cookie_name, ""))
    rows = context.identity.list_sessions(db, user.id)
    items = [
        SessionItemResponse(
            id=row.id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            current=row.token_hash == current_hash,
        )
        for row in rows
    ]
    return SessionListResponse(items=items, total=len(items))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[CsrfGuard],
)
def revoke_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> Response:
    if not context.identity.revoke_session_by_id(db, user.id, session_id):
        # Another user's session id and a nonexistent one answer identically.
        raise NotFoundError("الجلسة غير موجودة.")
    context.audit.record(
        db,
        action="session_revoked",
        actor_id=user.id,
        target_type="session",
        target_id=str(session_id),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/account-state", response_model=AccountStateResponse)
def account_state(
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> AccountStateResponse:
    verified = context.identity.email_is_verified(db, user.id)
    configured = context.settings.email_delivery_configured
    return AccountStateResponse(
        email=user.email,
        email_verified=verified,
        email_delivery_configured=configured,
        # The UI needs to know which of the two "not available" reasons applies.
        password_reset_available=configured,
    )
