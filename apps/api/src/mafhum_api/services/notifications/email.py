"""Outbound email, with the delivery mechanism behind an explicit interface.

Three providers implement one protocol:

* :class:`DisabledEmailSender` — the default. It refuses to deliver and says so.
  A route that needs mail must tell the user that mail is not configured rather
  than report a success that never happened.
* :class:`FileEmailSender` — development delivery. Each rendered message is
  written as a ``.eml`` file (mode 0600) inside a gitignored directory, which is
  how a developer completes a reset flow without an SMTP server. It deliberately
  does *not* log the message: the application's log formatter redacts anything
  that looks like a token, so a link routed through the log would either be
  scrubbed (useless) or would have to defeat the redaction (worse).
* :class:`SmtpEmailSender` — real delivery over TLS. Credentials are read from
  configuration, never logged.

Messages are *stored* in the ``email_outbox`` table by :class:`OutboxService`
and delivered by :meth:`OutboxService.deliver_pending`, which is invoked from a
bounded scheduled pass. Enqueueing and delivery are separate so that a slow or
unreachable mail server cannot hold a request open, and so a failed delivery is
retried from durable state instead of being lost with the process.

Bodies are rendered at delivery time from the row's template and payload. The
reset token itself is never part of that payload; see ``account_lifecycle``.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mafhum_api.config import Settings
from mafhum_api.db.models import EmailOutbox, OutboxStatus

logger = logging.getLogger(__name__)

TEMPLATE_PASSWORD_RESET = "password_reset"  # noqa: S105 - a template name, not a secret
TEMPLATE_EMAIL_VERIFICATION = "email_verification"


class EmailDeliveryError(RuntimeError):
    """Delivery failed. The message stays queued for a bounded retry."""


@runtime_checkable
class EmailSender(Protocol):
    """Deliver one rendered message."""

    @property
    def available(self) -> bool:
        """Whether this sender can actually deliver anything."""

    def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise :class:`EmailDeliveryError`."""


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    body: str
    # Whether the body is HTML. Plain text is always provided as a fallback.
    html: bool = False


class DisabledEmailSender:
    available = False

    def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError("email delivery is not configured")


class FileEmailSender:
    """Development sender: one file per message, never a network call."""

    available = True

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def send(self, message: EmailMessage) -> None:
        try:
            self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.eml"
            target = self._directory / name
            # O_EXCL + 0600: the message (which contains a live token) is never
            # world-readable and never overwrites another message.
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(message.as_bytes())
        except OSError as exc:
            raise EmailDeliveryError(type(exc).__name__) from exc
        # Only metadata reaches the log.
        logger.info(
            "email written to file",
            extra={"event": "email_file_delivery", "template": "filesystem"},
        )


class SmtpEmailSender:
    """Real SMTP delivery over STARTTLS, implicit TLS, or a trusted relay."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        security: str,
        from_address: str,
        timeout: float,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._security = security
        self._from = from_address
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._host.strip())

    def send(self, message: EmailMessage) -> None:
        if not self.available:
            raise EmailDeliveryError("smtp host is not configured")
        if "From" not in message:
            message["From"] = formataddr(("MAFHUM", self._from))
        if "Message-ID" not in message:
            message["Message-ID"] = make_msgid(domain="mafhum.local")
        try:
            if self._security == "ssl":
                context = ssl.create_default_context()
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=context
                )
            else:
                client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
            with client as smtp:
                smtp.ehlo()
                if self._security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if self._username:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # The exception text can contain the server banner or a hostname;
            # only a bounded, non-credential summary is persisted.
            raise EmailDeliveryError(type(exc).__name__) from exc


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_delivery == "smtp" and settings.smtp_host.strip():
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
            from_address=settings.smtp_from_address,
            timeout=settings.smtp_timeout_seconds,
        )
    if settings.email_delivery == "file":
        return FileEmailSender(settings.email_file_dir)
    return DisabledEmailSender()


# --------------------------------------------------------------------- rendering


def _payload_int(payload: Mapping[str, object], key: str, default: int) -> int:
    """Read an integer out of a JSON payload without trusting its type."""
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _greeting(display_name: str) -> str:
    name = display_name.strip()
    return f"مرحبًا {name}،" if name else "مرحبًا،"


def render_template(
    template: str, payload: Mapping[str, object], *, display_name: str
) -> RenderedEmail:
    """Render one of the supported templates.

    Everything here is fixed text plus a server-generated URL. No value from a
    user's document, filename or message ever enters a mail body.
    """
    link = str(payload.get("link", ""))
    if template == TEMPLATE_PASSWORD_RESET:
        ttl = _payload_int(payload, "ttl_minutes", 60)
        body = (
            f"{_greeting(display_name)}\n\n"
            "وصلنا طلب لإعادة تعيين كلمة المرور لحسابك في مَفْهُوم.\n"
            f"الرابط صالح لمدة {ttl} دقيقة ولمرة واحدة فقط:\n\n"
            f"{link}\n\n"
            "إذا لم تطلب هذا، يمكنك تجاهل الرسالة: كلمة المرور الحالية تبقى كما هي،\n"
            "ولن يُفعّل الرابط إلا إذا فُتح واستُخدم منك.\n\n"
            "فريق مَفْهُوم"
        )
        return RenderedEmail(subject="إعادة تعيين كلمة المرور — مَفْهُوم", body=body)
    if template == TEMPLATE_EMAIL_VERIFICATION:
        ttl_hours = max(1, _payload_int(payload, "ttl_minutes", 60) // 60)
        body = (
            f"{_greeting(display_name)}\n\n"
            "شكرًا لتسجيلك في مَفْهُوم. لتأكيد بريدك الإلكتروني افتح الرابط التالي:\n\n"
            f"{link}\n\n"
            f"الرابط صالح لمدة {ttl_hours} ساعة تقريبًا. تأكيد البريد يحمي حسابك،\n"
            "لأنه الطريقة الوحيدة لاستعادة كلمة المرور إذا نسيتها.\n\n"
            "فريق مَفْهُوم"
        )
        return RenderedEmail(subject="تأكيد البريد الإلكتروني — مَفْهُوم", body=body)
    raise EmailDeliveryError(f"unknown email template: {template}")


# ----------------------------------------------------------------------- outbox


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    attempted: int
    sent: int
    failed: int
    skipped: bool


class OutboxService:
    """Enqueue messages in a transaction; deliver them in a bounded pass."""

    def __init__(
        self,
        sender: Callable[[], EmailSender],
        *,
        batch_size: int = 20,
        max_attempts: int = 5,
    ) -> None:
        self._sender_factory = sender
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    @property
    def sender(self) -> EmailSender:
        return self._sender_factory()

    def enqueue(
        self,
        db: Session,
        *,
        recipient: str,
        template: str,
        payload: Mapping[str, object],
        user_id: uuid.UUID | None = None,
    ) -> EmailOutbox:
        row = EmailOutbox(
            user_id=user_id,
            recipient=recipient,
            template=template,
            payload=dict(payload),
            status=OutboxStatus.PENDING.value,
        )
        db.add(row)
        db.flush()
        return row

    def deliver_pending(self, db: Session) -> DeliveryReport:
        """Deliver queued messages, oldest first. Bounded by ``batch_size``.

        Rows are claimed by moving them to ``sending`` in the same statement that
        selects them, so two schedulers running concurrently do not send the same
        message twice.
        """
        sender = self.sender
        if not sender.available:
            return DeliveryReport(attempted=0, sent=0, failed=0, skipped=True)

        claimable = or_(
            EmailOutbox.status == OutboxStatus.PENDING.value,
            EmailOutbox.status == OutboxStatus.FAILED.value,
        )
        rows = list(
            db.scalars(
                select(EmailOutbox)
                .where(claimable, EmailOutbox.attempts < self._max_attempts)
                .order_by(EmailOutbox.created_at)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        if not rows:
            return DeliveryReport(attempted=0, sent=0, failed=0, skipped=False)

        sent = 0
        failed = 0
        for row in rows:
            row.status = OutboxStatus.SENDING.value
            row.attempts = row.attempts + 1
            db.flush()
            try:
                message = self._build_message(row)
                sender.send(message)
            except EmailDeliveryError as exc:
                row.status = OutboxStatus.FAILED.value
                row.last_error = str(exc)[:200]
                failed += 1
                logger.warning(
                    "email delivery failed",
                    extra={"event": "email_failed", "template": row.template},
                )
            except Exception:
                row.status = OutboxStatus.FAILED.value
                row.last_error = "unexpected_error"
                failed += 1
                logger.exception("unexpected email failure", extra={"event": "email_failed"})
            else:
                row.status = OutboxStatus.SENT.value
                row.sent_at = func.now()
                row.last_error = None
                sent += 1
        db.flush()
        return DeliveryReport(attempted=len(rows), sent=sent, failed=failed, skipped=False)

    def _build_message(self, row: EmailOutbox) -> EmailMessage:
        rendered = render_template(row.template, row.payload, display_name="")
        message = EmailMessage()
        message["To"] = row.recipient
        message["Subject"] = rendered.subject
        message.set_content(rendered.body)
        return message
