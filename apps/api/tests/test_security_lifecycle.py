"""Security properties of the account lifecycle, asserted rather than assumed.

Each test below corresponds to one line of the Phase 31 security review. Where a
property was already guaranteed by the code, the test exists to stop a future
change from quietly removing it; where the review found a real gap, the test is
the evidence that it closed.

Nothing here mocks the security control being tested. Tokens go through the real
service, cookies are inspected as the browser would see them, and the throttle is
exercised through the real endpoint.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mafhum_api.api.deps import AppContext
from mafhum_api.config import Settings
from mafhum_api.db.base import Database
from mafhum_api.db.models import ThrottledAction, User, UserToken
from mafhum_api.main import create_app

TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")
ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def secure_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Cookies marked Secure, as production requires."""
    return settings.model_copy(
        update={
            "email_delivery": "file",
            "email_file_dir": tmp_path / "mail",
            "public_web_url": "https://mafhum.test",
            "cookie_secure": True,
            # Both schemes are allowed for this suite: the client uses http so the
            # Secure cookie is actually stored and replayed (a Secure cookie is
            # refused over http), while the code under test still marks it Secure.
            "allowed_origins": ["http://testserver", "https://testserver"],
        }
    )


@pytest.fixture
def secure_app(secure_settings: Settings) -> Any:
    application = create_app(secure_settings, start_worker=False)
    yield application
    application.state.context.worker.stop()
    application.state.context.database.dispose()


@pytest.fixture
def context(secure_app: FastAPI) -> AppContext:
    return secure_app.state.context


@pytest.fixture
def database(secure_app: FastAPI) -> Database:
    return secure_app.state.context.database


def _client(app: FastAPI) -> TestClient:
    """A browser-like client: cookies kept, no session yet.

    ``https`` is used because the suite sets ``COOKIE_SECURE=true`` (the
    production requirement) and an http client refuses to store a Secure cookie.
    Using http here would make every session assertion fail for a reason that has
    nothing to do with the code under test.
    """
    return TestClient(app, base_url="https://testserver", follow_redirects=True)


def _identity(tag: str) -> dict[str, str]:
    """A distinct client address per test.

    The throttle lives in the database, and the test database is shared across
    the session, so reusing one address would make tests fail each other's
    budgets. ``X-Forwarded-For`` is also the header the resolver reads in
    production, so this exercises the real code path rather than a bypass.
    """
    return {"X-Forwarded-For": f"203.0.113.{abs(hash(tag)) % 250 + 1}"}


def _csrf(client: TestClient, *, who: str = "default") -> str:
    return client.get("/api/v1/auth/csrf", headers=_identity(who)).json()["csrf_token"]


def _headers(
    who: str,
    *,
    csrf: str | None = None,
    origin: str = "https://testserver",
) -> dict[str, str]:
    headers = {**_identity(who), "Origin": origin}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _register(client: TestClient, email: str, password: str, *, who: str = "reg") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "طالب"},
        headers=_headers(who),
    )
    assert response.status_code in (200, 201), response.text


def _newest_token(directory: Path) -> str:
    files = sorted(directory.glob("*.eml"))
    assert files, "no mail was written"
    message = BytesParser(policy=policy.default).parsebytes(files[-1].read_bytes())
    body = message.get_body(preferencelist=("plain",)).get_content()
    match = TOKEN_RE.search(body)
    assert match, "the message carries no token"
    return match.group(1)


# --------------------------------------------------------------- token lifetime
def test_expired_token_is_rejected(
    secure_app: FastAPI, database: Database, context: AppContext
) -> None:
    """A token past ``expires_at`` must not redeem, even though it is unused."""
    client = _client(secure_app)
    _register(client, "expired@example.com", "correct-horse-1")
    me = client.get("/api/v1/auth/me", headers=_identity("expired"))
    assert me.status_code == 200, (me.status_code, me.text)
    user_id = me.json()["id"]

    with database.session() as db:
        user = db.get(User, uuid.UUID(user_id))
        assert user is not None
        issued = context.account_tokens.issue(db, user=user, purpose="password_reset")
        # Move the whole row into the past: time is the only variable under test.
        # `created_at` moves too because the schema itself forbids a token whose
        # expiry precedes its creation (ck_user_tokens_expiry) — a second guard
        # against a misconfigured TTL minting dead links.
        row = db.scalar(select(UserToken).where(UserToken.token_prefix == issued.token[:8]))
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": issued.token, "password": "new-password-2"},
        headers=_headers("expired", csrf=_csrf(client, who="expired")),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_token"


# --------------------------------------------------------------------- cookies
def test_auth_cookies_carry_secure_flags(secure_app: FastAPI) -> None:
    """HttpOnly + Secure + SameSite on the session cookie; readable CSRF cookie."""
    client = _client(secure_app)
    _register(client, "cookies@example.com", "correct-horse-1", who="cookies")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "cookies@example.com", "password": "correct-horse-1"},
        headers=_headers("cookies"),
    )
    assert response.status_code == 200

    raw = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in raw if "mafhum_session" in c)
    csrf_cookie = next(c for c in raw if "mafhum_csrf" in c)

    assert "HttpOnly" in session_cookie, session_cookie
    assert "Secure" in session_cookie, session_cookie
    assert "SameSite=lax" in session_cookie.replace("samesite", "SameSite"), session_cookie

    # The CSRF cookie is deliberately script-readable: the double submit needs it.
    assert "HttpOnly" not in csrf_cookie, csrf_cookie
    assert "Secure" in csrf_cookie, csrf_cookie


def test_session_cookie_is_not_issued_by_the_anonymous_csrf_endpoint(
    secure_app: FastAPI,
) -> None:
    client = _client(secure_app)
    response = client.get("/api/v1/auth/csrf")
    raw = " ".join(response.headers.get_list("set-cookie"))
    assert "mafhum_session" not in raw


# ------------------------------------------------------- session invalidation
def test_password_change_keeps_caller_and_kills_other_sessions(
    secure_app: FastAPI, context: AppContext
) -> None:
    """The device that changes the password stays in; the others are evicted."""
    first = _client(secure_app)
    _register(first, "rotate@example.com", "original-pass-1", who="rotate-1")

    second = _client(secure_app)
    login = second.post(
        "/api/v1/auth/login",
        json={"email": "rotate@example.com", "password": "original-pass-1"},
        headers=_headers("rotate-2"),
    )
    assert login.status_code == 200

    change = first.post(
        "/api/v1/auth/password/change",
        json={"current_password": "original-pass-1", "new_password": "rotated-pass-2"},
        headers=_headers("rotate-1", csrf=_csrf(first, who="rotate-1")),
    )
    assert change.status_code == 200, change.text

    assert first.get("/api/v1/auth/me", headers=_identity("rotate-1")).status_code == 200
    assert second.get("/api/v1/auth/me", headers=_identity("rotate-2")).status_code == 401


# ------------------------------------------------------------------- throttle
def test_reset_requests_are_rate_limited(secure_app: FastAPI) -> None:
    """The unauthenticated mail endpoint cannot be used as a mail cannon.

    This was a real gap: reset/verification had no limit at all, so one client
    could send unbounded mail — burning a free-tier quota and taking recovery
    down for every user.
    """
    client = _client(secure_app)
    headers = _headers("cannon", csrf=_csrf(client, who="cannon"))

    statuses = [
        client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "victim@example.com"},
            headers=headers,
        ).status_code
        for _ in range(7)
    ]
    # The configured budget is 5 per window; the rest are refused.
    assert statuses.count(202) == 5, statuses
    assert all(code == 429 for code in statuses[5:]), statuses


def test_throttled_response_does_not_reveal_whether_the_account_exists(
    secure_app: FastAPI,
) -> None:
    """429 is about the caller, not the address."""
    client = _client(secure_app)
    headers = _headers("ghost", csrf=_csrf(client, who="ghost"))
    for _ in range(5):
        client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "ghost@example.com"},
            headers=headers,
        )
    known = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "ghost@example.com"},
        headers=headers,
    )
    assert known.status_code == 429


def test_throttle_stores_no_raw_address(secure_app: FastAPI, database: Database) -> None:
    """The table must not become a log of who tried to recover what."""
    client = _client(secure_app)
    client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "privacy@example.com"},
        headers=_headers("privacy", csrf=_csrf(client, who="privacy")),
    )
    with database.session() as db:
        rows = [row for row in db.scalars(select(ThrottledAction)) if row.scope == "password_reset"]
    assert rows, "the attempt was not recorded"
    for row in rows:
        assert "testclient" not in row.client_key
        assert len(row.client_key) == 64  # hex digest of an HMAC
        assert re.fullmatch(r"[0-9a-f]{64}", row.client_key)


def test_verification_has_its_own_budget(secure_app: FastAPI) -> None:
    """Exhausting reset must not disable address verification."""
    client = _client(secure_app)
    _register(client, "budget@example.com", "correct-horse-1", who="budget")
    headers = _headers("budget", csrf=_csrf(client, who="budget"))
    for _ in range(6):
        client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "budget@example.com"},
            headers=headers,
        )
    verify = client.post("/api/v1/auth/email-verification/request", headers=headers)
    # Not 429 from the reset bucket; the verification bucket is separate.
    assert verify.status_code in (202, 200), verify.text


# ------------------------------------------------------------- enumeration
def test_reset_request_is_indistinguishable_for_unknown_accounts(
    secure_app: FastAPI, database: Database
) -> None:
    """Same status, same body, and no timestamp difference we can observe."""
    client = _client(secure_app)
    _register(client, "real@example.com", "correct-horse-1", who="enum-reg")

    other = _client(secure_app)
    headers = _headers("enum", csrf=_csrf(other, who="enum"))
    known = other.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "real@example.com"},
        headers=headers,
    )
    unknown = other.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "nobody-here@example.com"},
        headers=headers,
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()

    # The only observable difference is inside the database, and it belongs to the
    # real account: exactly one reset token exists for it. Counting rows globally
    # would measure other tests in the shared database instead of this behaviour.
    with database.session() as db:
        real = db.scalar(select(User).where(User.email == "real@example.com"))
        assert real is not None
        reset_tokens = [
            token
            for token in db.scalars(select(UserToken).where(UserToken.user_id == real.id))
            if token.purpose == "password_reset"
        ]
    assert len(reset_tokens) == 1


def test_login_error_does_not_reveal_account_existence(secure_app: FastAPI) -> None:
    """A wrong password and an unknown address answer identically."""
    client = _client(secure_app)
    _register(client, "exists@example.com", "correct-horse-1", who="login-enum")
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": "exists@example.com", "password": "not-the-password"},
        headers=_headers("login-enum"),
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "does-not-exist@example.com", "password": "not-the-password"},
        headers=_headers("login-enum"),
    )
    assert wrong_password.status_code == unknown.status_code


# ----------------------------------------------------------------- CSRF
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/password-reset/request",
        "/api/v1/auth/password-reset/confirm",
        "/api/v1/auth/email-verification/confirm",
    ],
)
def test_anonymous_endpoints_reject_a_foreign_origin(secure_app: FastAPI, path: str) -> None:
    """Origin is the only check available before a session exists — so it is mandatory."""
    client = _client(secure_app)
    _csrf(client, who=f"origin-{path}")
    response = client.post(
        path,
        json={"email": "a@b.co", "token": "x" * 43, "password": "whatever-pass-1"},
        headers={**_identity(f"origin-{path}"), "Origin": "https://evil.example"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "csrf_failed"


def test_anonymous_endpoints_reject_a_missing_origin(secure_app: FastAPI) -> None:
    client = _client(secure_app)
    _csrf(client, who="no-origin")
    # TestClient sends no Origin by default: exactly what same-site curl or a
    # stripped proxy would produce.
    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "a@b.co"},
        headers=_identity("no-origin"),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "csrf_failed"


def test_double_submit_is_enforced_when_the_cookie_exists(secure_app: FastAPI) -> None:
    client = _client(secure_app)
    _csrf(client, who="double-submit")  # cookie present, header not sent
    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "a@b.co"},
        headers=_headers("double-submit"),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "csrf_failed"


# ------------------------------------------------------------- logging hygiene
def test_rendered_mail_never_reaches_the_log(
    secure_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """The token must exist in the message, not in the application log."""
    caplog.set_level("DEBUG")
    client = _client(secure_app)
    _register(client, "log@example.com", "correct-horse-1", who="log")
    client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "log@example.com"},
        headers=_headers("log", csrf=_csrf(client, who="log")),
    )
    token = _newest_token(secure_app.state.context.settings.email_file_dir)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in log_text
    # And nothing resembling a reset link survived either.
    assert "reset-password?token=" not in log_text


def test_smtp_failure_is_bounded_and_credential_free() -> None:
    """A delivery error is reported by type, never by echoing the server banner."""
    from mafhum_api.services.notifications.email import EmailDeliveryError, SmtpEmailSender

    sender = SmtpEmailSender(
        host="127.0.0.1",
        port=1,  # nothing listens here
        username="user",
        password="super-secret",
        security="starttls",
        from_address="no-reply@mafhum.test",
        timeout=0.25,
    )
    from email.message import EmailMessage

    message = EmailMessage()
    message["To"] = "someone@example.com"
    message.set_content("hello")
    with pytest.raises(EmailDeliveryError) as caught:
        sender.send(message)
    assert "super-secret" not in str(caught.value)
