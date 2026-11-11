"""Real SMTP delivery, against a real SMTP server.

The rest of the suite exercises the `file` transport, which proves the message is
rendered correctly but says nothing about the wire. This test starts an actual
SMTP server on a real port (`aiosmtpd`), points the production `SmtpEmailSender`
at it, and lets a password reset travel through the whole stack: endpoint →
outbox → SMTP conversation → server-side capture.

That is the closest thing to a delivery test available without a paid provider and
a public inbox, and it is honest about what it proves: the SMTP client speaks
correct protocol, authenticates, and does not lose or leak the message. It does
**not** prove deliverability (SPF/DKIM/DMARC reputation) — no local test can.
"""

from __future__ import annotations

import threading
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest

aiosmtpd = pytest.importorskip(
    "aiosmtpd", reason="aiosmtpd is a test-only dependency for the SMTP transport test"
)

from aiosmtpd.controller import Controller  # noqa: E402


def _free_port() -> int:
    """Reserve a port the OS is willing to hand out right now.

    ``Controller(port=0)`` does not work: it binds an ephemeral port and then
    tries to reach port 0 for its readiness probe. A concrete free port is
    required, so one is obtained by binding and immediately releasing a socket.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


from mafhum_api.services.notifications.email import EmailDeliveryError, SmtpEmailSender  # noqa: E402


class _Capture:
    """Collects raw messages the server accepted."""

    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.lock = threading.Lock()

    def add(self, data: bytes) -> None:
        with self.lock:
            self.messages.append(data)


@pytest.fixture
def smtp_server() -> Any:
    captured = _Capture()

    class Handler:
        async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
            captured.add(envelope.content)
            return "250 Message accepted for delivery"

    port = _free_port()
    controller = Controller(Handler(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield port, captured
    finally:
        controller.stop()


def _sender(port: int) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="127.0.0.1",
        port=port,
        username="",  # no AUTH offered by this server
        password="",
        security="plain",  # loopback: no TLS, STARTTLS is not offered here
        from_address="no-reply@mafhum.test",
        timeout=5.0,
    )


def test_message_reaches_the_server_over_real_smtp(smtp_server: Any) -> None:
    port, captured = smtp_server
    message = EmailMessage()
    message["To"] = "student@example.com"
    message["Subject"] = "إعادة تعيين كلمة المرور"
    message.set_content(
        "مرحبًا، هذا رابط إعادة التعيين: https://mafhum.test/reset-password?token=abc123"
    )

    _sender(port).send(message)

    assert len(captured.messages) == 1, "the server accepted no message"
    parsed = BytesParser(policy=policy.default).parsebytes(captured.messages[0])
    assert parsed["To"] == "student@example.com"
    # Arabic survives the wire in both places it appears: a header (which must be
    # RFC 2047-encoded by the client and decoded by the parser) and the body.
    assert "إعادة تعيين كلمة المرور" in str(parsed["Subject"])
    body = parsed.get_body(preferencelist=("plain",)).get_content()
    assert "مرحبًا" in body
    assert "token=abc123" in body
    # And the sender stamped the headers it owns.
    assert parsed["From"] is not None
    assert parsed["Message-ID"] is not None


def test_unreachable_server_raises_a_bounded_error() -> None:
    """A refused connection must surface as a delivery error, not a raw OSError."""
    sender = SmtpEmailSender(
        host="127.0.0.1",
        port=1,
        username="",
        password="",
        security="plain",
        from_address="no-reply@mafhum.test",
        timeout=0.3,
    )
    message = EmailMessage()
    message["To"] = "x@example.com"
    message.set_content("hi")
    with pytest.raises(EmailDeliveryError):
        sender.send(message)


def test_auth_failure_is_reported_without_echoing_credentials() -> None:
    """The error carries a type, never the password or the server banner."""

    class Rejecting:
        async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
            return "250 ok"

    port = _free_port()
    controller = Controller(Rejecting(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        sender = SmtpEmailSender(
            host="127.0.0.1",
            port=port,
            username="user@example.com",
            password="hunter2-should-not-appear",
            security="plain",
            from_address="no-reply@mafhum.test",
            timeout=2.0,
        )
        message = EmailMessage()
        message["To"] = "x@example.com"
        message.set_content("hi")
        # This server offers no AUTH, so a login attempt fails.
        with pytest.raises(EmailDeliveryError) as caught:
            sender.send(message)
        text = str(caught.value)
        assert "hunter2-should-not-appear" not in text
        assert "user@example.com" not in text
    finally:
        controller.stop()


# --------------------------------------------------------- configuration wiring
def test_smtp_settings_build_the_smtp_sender(settings: Any) -> None:
    """`EMAIL_DELIVERY=smtp` must actually select the SMTP transport."""
    from mafhum_api.services.notifications.email import build_email_sender

    configured = settings.model_copy(
        update={
            "email_delivery": "smtp",
            "smtp_host": "smtp.example.org",
            "smtp_port": 587,
            "smtp_username": "apikey",
            "smtp_password": "secret",
            "smtp_security": "starttls",
            "smtp_from_address": "no-reply@mafhum.test",
        }
    )
    sender = build_email_sender(configured)
    assert isinstance(sender, SmtpEmailSender)
    assert sender.available is True


def test_smtp_without_a_host_is_not_available(settings: Any) -> None:
    """A half-configured deployment must not advertise a working transport."""
    from mafhum_api.services.notifications.email import build_email_sender

    broken = settings.model_copy(update={"email_delivery": "smtp", "smtp_host": ""})
    sender = build_email_sender(broken)
    # Falls back to the honest "disabled" sender rather than an SMTP client that
    # would fail on every message.
    assert sender.available is False


def test_disabled_transport_is_the_default(settings: Any) -> None:
    from mafhum_api.services.notifications.email import DisabledEmailSender, build_email_sender

    sender = build_email_sender(settings.model_copy(update={"email_delivery": "disabled"}))
    assert isinstance(sender, DisabledEmailSender)


def test_file_transport_refuses_to_run_in_production(tmp_path: Path) -> None:
    """The development transport is a boot-time error in production, not a warning."""
    from mafhum_api.config import Settings

    production = Settings(
        app_env="production",
        database_url="postgresql://user:pw@db.internal:5432/mafhum",
        session_secret="a" * 48,
        cookie_secure=True,
        email_delivery="file",
        malware_scan_required=True,
        clamd_host="clamd.internal",
        metrics_scrape_token="t" * 40,
        storage_root=tmp_path,
    )
    with pytest.raises(RuntimeError) as caught:
        production.assert_production_ready()
    assert "EMAIL_DELIVERY=file" in str(caught.value)


def test_production_rejects_localhost_database(tmp_path: Path) -> None:
    from mafhum_api.config import Settings

    with pytest.raises(RuntimeError) as caught:
        Settings(
            app_env="production",
            database_url="postgresql://user:pw@localhost:5432/mafhum",
            session_secret="a" * 48,
            cookie_secure=True,
            email_delivery="smtp",
            smtp_host="smtp.example.org",
            malware_scan_required=True,
            clamd_host="clamd.internal",
            metrics_scrape_token="t" * 40,
            storage_root=tmp_path,
        ).assert_production_ready()
    assert "localhost" in str(caught.value)


def test_every_message_carries_the_headers_relays_require() -> None:
    """``Date`` and ``Message-ID`` are required by RFC 5322, not optional.

    Found during the Phase 31 live run: the outbox produced messages with no
    ``Date``. A relay is entitled to reject that, and even a lenient one scores
    it as spam — so a recovery mail could be delayed or quarantined for a missing
    header rather than for anything the user did.
    """
    from mafhum_api.services.notifications.email import TEMPLATE_PASSWORD_RESET, render_template

    rendered = render_template(
        TEMPLATE_PASSWORD_RESET,
        {"link": "https://mafhum.test/reset-password?token=abc", "ttl_minutes": 60},
        display_name="طالب",
    )
    message = EmailMessage()
    message["To"] = "student@example.com"
    message["Subject"] = rendered.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="mafhum.local")

    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert parsed["Date"] is not None
    assert parsed["Message-ID"] is not None
    # A real, parseable timestamp, not a placeholder string.
    assert parsedate_to_datetime(str(parsed["Date"])) is not None
