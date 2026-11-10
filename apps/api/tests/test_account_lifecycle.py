"""Account lifecycle: reset, verification, password change, session management.

These tests drive the real HTTP surface through the real services. Nothing here
stubs the token table or the delivery path: the transport under test is the real
``file`` sender, which writes a genuine RFC 5322 message to disk, and the token is
read back out of that message. What is exercised is therefore the flow a user
performs, not a stand-in for it.

A note on why the log is not used as the delivery channel in tests: the
application's log formatter redacts anything shaped like a token on purpose, so a
live link cannot and should not survive a trip through the log.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mafhum_api.api.deps import AppContext
from mafhum_api.config import Settings
from mafhum_api.main import create_app

TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


@pytest.fixture
def mail_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings with a genuine file transport enabled."""
    return settings.model_copy(
        update={
            "email_delivery": "file",
            "email_file_dir": tmp_path / "mail",
            "public_web_url": "https://mafhum.test",
        }
    )


@pytest.fixture
def mail_app(mail_settings: Settings) -> Iterator[FastAPI]:
    application = create_app(mail_settings, start_worker=False)
    yield application
    application.state.context.worker.stop()
    application.state.context.database.dispose()


@pytest.fixture
def mail_context(mail_app: FastAPI) -> AppContext:
    return mail_app.state.context


@pytest.fixture
def mail_client(mail_app: FastAPI) -> Iterator[TestClient]:
    from sqlalchemy import text

    from mafhum_api.db import models

    database = mail_app.state.context.database
    table_names = [table.name for table in models.Base.metadata.sorted_tables]
    with database.engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE")
        )
    with TestClient(mail_app, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def mail_dir(mail_settings: Settings) -> Path:
    return mail_settings.email_file_dir


def _register(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "طالب"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/session", headers={"Origin": "http://testserver"})
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def _post(client: TestClient, url: str, json: dict[str, Any] | None = None) -> Any:
    return client.post(
        url,
        json=json or {},
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf(client)},
    )


def _delivered_mail(directory: Path) -> list[str]:
    """Every delivered message, oldest first, body decoded.

    The files are real RFC 5322 messages, so they are parsed rather than
    string-matched: the body is base64 for Arabic content, and grepping the raw
    file would find nothing.
    """
    if not directory.is_dir():
        return []
    parsed: list[tuple[float, str]] = []
    for entry in sorted(directory.glob("*.eml")):
        with entry.open("rb") as handle:
            message = BytesParser(policy=policy.default).parse(handle)
        body_part = message.get_body(preferencelist=("plain",))
        body = body_part.get_content() if body_part is not None else ""
        parsed.append((entry.stat().st_mtime, f"{message['Subject']}\n{body}"))
    # Newest first: "the link we just asked for" is the last one delivered, and
    # asserting on the first would silently test a superseded token.
    return [text for _mtime, text in reversed(parsed)]


def _token_from_mail(directory: Path, marker: str) -> str:
    for message in _delivered_mail(directory):
        if marker in message:
            match = TOKEN_RE.search(message)
            if match:
                return match.group(1)
    raise AssertionError(f"no {marker} link in delivered mail: {_delivered_mail(directory)}")


class TestPasswordReset:
    def test_unknown_address_answers_identically(
        self, mail_client: TestClient, mail_dir: Path
    ) -> None:
        _register(mail_client, "known@example.com", "correct-horse-battery")
        _csrf(mail_client)

        known = _post(
            mail_client, "/api/v1/auth/password-reset/request", {"email": "known@example.com"}
        )
        unknown = _post(
            mail_client, "/api/v1/auth/password-reset/request", {"email": "nobody@example.com"}
        )

        assert known.status_code == 202
        assert unknown.status_code == 202
        # The body must not reveal whether the account exists. The only
        # difference is a queued mail the caller cannot see.
        assert known.json() == unknown.json()

    def test_full_reset_flow_changes_password_and_revokes_sessions(
        self, mail_client: TestClient, mail_dir: Path
    ) -> None:
        _register(mail_client, "student@example.com", "original-password-1")
        _csrf(mail_client)

        response = _post(
            mail_client, "/api/v1/auth/password-reset/request", {"email": "student@example.com"}
        )
        assert response.status_code == 202, response.text
        token = _token_from_mail(mail_dir, "reset-password?token=")

        confirm = _post(
            mail_client,
            "/api/v1/auth/password-reset/confirm",
            {"token": token, "password": "brand-new-password-2"},
        )
        assert confirm.status_code == 200, confirm.text

        # Old password no longer works, new one does. Uses fresh clients so each
        # login has its own cookie jar.
        fresh = TestClient(mail_client.app, base_url="http://testserver")
        old = fresh.post(
            "/api/v1/auth/login",
            json={"email": "student@example.com", "password": "original-password-1"},
            headers={"Origin": "http://testserver"},
        )
        assert old.status_code == 401
        new = fresh.post(
            "/api/v1/auth/login",
            json={"email": "student@example.com", "password": "brand-new-password-2"},
            headers={"Origin": "http://testserver"},
        )
        assert new.status_code == 200

    def test_token_is_single_use(self, mail_client: TestClient, mail_dir: Path) -> None:
        _register(mail_client, "once@example.com", "original-password-1")
        _csrf(mail_client)
        _post(mail_client, "/api/v1/auth/password-reset/request", {"email": "once@example.com"})
        token = _token_from_mail(mail_dir, "reset-password?token=")

        first = _post(
            mail_client,
            "/api/v1/auth/password-reset/confirm",
            {"token": token, "password": "first-new-password"},
        )
        assert first.status_code == 200, first.text

        # The reset revoked every session, including this one — that is the
        # behaviour being tested. Sign back in before retrying the used link.
        reused = TestClient(mail_client.app, base_url="http://testserver")
        login = reused.post(
            "/api/v1/auth/login",
            json={"email": "once@example.com", "password": "first-new-password"},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200
        second = _post(
            reused,
            "/api/v1/auth/password-reset/confirm",
            {"token": token, "password": "second-new-password"},
        )
        assert second.status_code == 422
        # "Already used" is a different message from "invalid": a user needs to
        # know that requesting a new link is the right next step.
        assert "مستعمل" in second.json()["message"]

    def test_issuing_a_new_token_invalidates_the_previous_one(
        self, mail_client: TestClient, mail_dir: Path
    ) -> None:
        _register(mail_client, "supersede@example.com", "original-password-1")
        _csrf(mail_client)
        _post(
            mail_client, "/api/v1/auth/password-reset/request", {"email": "supersede@example.com"}
        )
        first_token = _token_from_mail(mail_dir, "reset-password?token=")
        _post(
            mail_client, "/api/v1/auth/password-reset/request", {"email": "supersede@example.com"}
        )
        second_token = _token_from_mail(mail_dir, "reset-password?token=")
        assert first_token != second_token

        stale = _post(
            mail_client,
            "/api/v1/auth/password-reset/confirm",
            {"token": first_token, "password": "from-stale-link-pw"},
        )
        assert stale.status_code == 422

    def test_reset_revokes_existing_sessions(self, mail_client: TestClient, mail_dir: Path) -> None:
        _register(mail_client, "session@example.com", "original-password-1")
        # A second, independent session (the "stolen laptop").
        other = TestClient(mail_client.app, base_url="http://testserver")
        login = other.post(
            "/api/v1/auth/login",
            json={"email": "session@example.com", "password": "original-password-1"},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200
        assert other.get("/api/v1/auth/me").status_code == 200

        _csrf(mail_client)
        _post(mail_client, "/api/v1/auth/password-reset/request", {"email": "session@example.com"})
        token = _token_from_mail(mail_dir, "reset-password?token=")
        confirm = _post(
            mail_client,
            "/api/v1/auth/password-reset/confirm",
            {"token": token, "password": "after-reset-password"},
        )
        assert confirm.status_code == 200
        assert other.get("/api/v1/auth/me").status_code == 401

    def test_unconfigured_delivery_is_reported_not_faked(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """With no transport configured the endpoint must say so."""
        assert app.state.context.settings.email_delivery == "disabled"
        _register(client, "nomail@example.com", "correct-horse-battery")
        _csrf(client)
        response = _post(
            client, "/api/v1/auth/password-reset/request", {"email": "nomail@example.com"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "email_delivery_unavailable"


class TestEmailVerification:
    def test_verification_flow_marks_the_account_verified(
        self, mail_client: TestClient, mail_dir: Path
    ) -> None:
        _register(mail_client, "verify@example.com", "correct-horse-battery")
        _csrf(mail_client)

        state = mail_client.get("/api/v1/auth/account-state")
        assert state.status_code == 200
        assert state.json()["email_verified"] is False
        assert state.json()["email_delivery_configured"] is True

        request = _post(mail_client, "/api/v1/auth/email-verification/request")
        assert request.status_code == 202, request.text
        token = _token_from_mail(mail_dir, "verify-email?token=")

        confirm = _post(mail_client, "/api/v1/auth/email-verification/confirm", {"token": token})
        assert confirm.status_code == 200, confirm.text

        assert mail_client.get("/api/v1/auth/account-state").json()["email_verified"] is True

        # Requesting again is a no-op with an honest message, not a second mail.
        again = _post(mail_client, "/api/v1/auth/email-verification/request")
        assert again.status_code == 202
        assert "مؤكَّد" in again.json()["message"] or "مؤكد" in again.json()["message"]

    def test_verification_requires_a_session(self, mail_client: TestClient) -> None:
        # No session: the dependency rejects before CSRF is even evaluated, so a
        # bare POST must be a 401 and never a 200.
        response = mail_client.post(
            "/api/v1/auth/email-verification/request", headers={"Origin": "http://testserver"}
        )
        assert response.status_code == 401

    def test_password_reset_token_cannot_verify_an_email(
        self, mail_client: TestClient, mail_dir: Path
    ) -> None:
        """Purposes are not interchangeable."""
        _register(mail_client, "purpose@example.com", "correct-horse-battery")
        _csrf(mail_client)
        _post(mail_client, "/api/v1/auth/password-reset/request", {"email": "purpose@example.com"})
        token = _token_from_mail(mail_dir, "reset-password?token=")

        response = _post(mail_client, "/api/v1/auth/email-verification/confirm", {"token": token})
        assert response.status_code == 422


class TestPasswordChange:
    def test_change_requires_the_current_password(self, mail_client: TestClient) -> None:
        _register(mail_client, "change@example.com", "correct-horse-battery")
        _csrf(mail_client)
        response = _post(
            mail_client,
            "/api/v1/auth/password/change",
            {"current_password": "wrong-password", "new_password": "another-password-1"},
        )
        assert response.status_code == 422
        assert "current_password" in response.json()["field_errors"]

    def test_change_keeps_this_session_and_ends_the_others(self, mail_client: TestClient) -> None:
        _register(mail_client, "keep@example.com", "correct-horse-battery")
        other = TestClient(mail_client.app, base_url="http://testserver")
        other.post(
            "/api/v1/auth/login",
            json={"email": "keep@example.com", "password": "correct-horse-battery"},
            headers={"Origin": "http://testserver"},
        )
        assert other.get("/api/v1/auth/me").status_code == 200

        _csrf(mail_client)
        response = _post(
            mail_client,
            "/api/v1/auth/password/change",
            {"current_password": "correct-horse-battery", "new_password": "new-password-here"},
        )
        assert response.status_code == 200, response.text
        # The caller keeps working; the other device is signed out.
        assert mail_client.get("/api/v1/auth/me").status_code == 200
        assert other.get("/api/v1/auth/me").status_code == 401

    def test_change_requires_csrf(self, mail_client: TestClient) -> None:
        _register(mail_client, "csrf@example.com", "correct-horse-battery")
        response = mail_client.post(
            "/api/v1/auth/password/change",
            json={"current_password": "correct-horse-battery", "new_password": "new-password-here"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "csrf_failed"


class TestSessionManagement:
    def test_lists_sessions_and_marks_the_current_one(self, mail_client: TestClient) -> None:
        _register(mail_client, "sessions@example.com", "correct-horse-battery")
        _csrf(mail_client)
        second = TestClient(mail_client.app, base_url="http://testserver")
        second.post(
            "/api/v1/auth/login",
            json={"email": "sessions@example.com", "password": "correct-horse-battery"},
            headers={"Origin": "http://testserver"},
        )

        listing = mail_client.get("/api/v1/auth/sessions")
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 2
        assert sum(1 for item in body["items"] if item["current"]) == 1

    def test_revoking_another_users_session_is_a_not_found(
        self, mail_app: FastAPI, mail_client: TestClient
    ) -> None:
        _register(mail_client, "owner@example.com", "correct-horse-battery")
        _csrf(mail_client)
        stranger = TestClient(mail_app, base_url="http://testserver")
        stranger.post(
            "/api/v1/auth/register",
            json={"email": "stranger@example.com", "password": "correct-horse-battery"},
            headers={"Origin": "http://testserver"},
        )
        victim_session_id = stranger.get("/api/v1/auth/sessions").json()["items"][0]["id"]

        response = mail_client.delete(
            f"/api/v1/auth/sessions/{victim_session_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf(mail_client)},
        )
        # Identical to a nonexistent id: no existence oracle.
        assert response.status_code == 404
        # And the victim is still signed in.
        assert stranger.get("/api/v1/auth/me").status_code == 200

    def test_revoking_own_session_signs_it_out(self, mail_client: TestClient) -> None:
        _register(mail_client, "selfrevoke@example.com", "correct-horse-battery")
        csrf = _csrf(mail_client)
        session_id = mail_client.get("/api/v1/auth/sessions").json()["items"][0]["id"]
        response = mail_client.delete(
            f"/api/v1/auth/sessions/{session_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 204
        assert mail_client.get("/api/v1/auth/me").status_code == 401


class TestTokenStorage:
    def test_tokens_are_never_stored_in_the_clear(
        self, mail_client: TestClient, mail_context: AppContext, mail_dir: Path
    ) -> None:
        from sqlalchemy import select

        from mafhum_api.db.models import UserToken

        _register(mail_client, "hash@example.com", "correct-horse-battery")
        _csrf(mail_client)
        _post(mail_client, "/api/v1/auth/password-reset/request", {"email": "hash@example.com"})
        token = _token_from_mail(mail_dir, "reset-password?token=")

        with mail_context.database.session() as db:
            rows = list(db.scalars(select(UserToken)))
            assert rows, "no token row was written"
            stored = {(row.token_hash, row.token_prefix) for row in rows}
            for token_hash, prefix in stored:
                assert token not in token_hash
                assert token_hash != token
                # The prefix is a locator, not a credential: it is a small
                # fraction of a 43-character token.
                assert token.startswith(prefix)
                assert len(prefix) == 8


class TestDocumentSearch:
    """In-document search: Arabic folding, ownership, and honest emptiness."""

    def test_search_finds_text_and_folds_arabic_variants(
        self, mail_client: TestClient, app: FastAPI
    ) -> None:
        _register(mail_client, "search@example.com", "correct-horse-battery")
        _csrf(mail_client)

        from mafhum_api.db.models import Document, DocumentStatus, DocumentTextSegment

        context = app.state.context
        with context.database.session() as db:
            from mafhum_api.db.models import User

            user = db.query(User).filter(User.email == "search@example.com").one()
            document = Document(
                owner_id=user.id,
                storage_key="search-key-1",
                display_filename="محاضرة.pdf",
                format="pdf",
                size_bytes=1024,
                status=DocumentStatus.READY.value,
                content_sha256="0" * 64,
                page_count=1,
                char_count=120,
                segment_count=2,
            )
            db.add(document)
            db.flush()
            db.add_all(
                [
                    DocumentTextSegment(
                        document_id=document.id,
                        ordinal=0,
                        page_number=1,
                        generation=1,
                        text="الإِمَارَة الجزائرية ومقاومة الاستعمار",
                        char_count=36,
                    ),
                    DocumentTextSegment(
                        document_id=document.id,
                        ordinal=1,
                        page_number=1,
                        generation=1,
                        text="Le protectorat français et la résistance",
                        char_count=42,
                    ),
                ]
            )
            db.commit()
            document_id = document.id

        # Vowelled document, unvowelled query, and an alef variant.
        vowelled = mail_client.get(f"/api/v1/documents/{document_id}/text?q=الامارة")
        assert vowelled.status_code == 200, vowelled.text
        body = vowelled.json()
        assert body["total"] == 1
        assert body["query"] == "الامارة"
        assert "الإِمَارَة" in body["items"][0]["text"]

        # Case-insensitive for the Latin side.
        latin = mail_client.get(f"/api/v1/documents/{document_id}/text?q=PROTECTORAT")
        assert latin.json()["total"] == 1

        # No match is an empty page, never a fallback to "everything".
        missing = mail_client.get(f"/api/v1/documents/{document_id}/text?q=zzz-nothing")
        assert missing.json()["total"] == 0
        assert missing.json()["items"] == []

    def test_search_does_not_cross_user_boundaries(
        self, mail_client: TestClient, app: FastAPI
    ) -> None:
        from mafhum_api.db.models import Document, DocumentStatus, DocumentTextSegment, User

        _register(mail_client, "owner@example.com", "correct-horse-battery")
        _csrf(mail_client)
        context = app.state.context
        with context.database.session() as db:
            owner = db.query(User).filter(User.email == "owner@example.com").one()
            document = Document(
                owner_id=owner.id,
                storage_key="search-key-2",
                display_filename="خاص.pdf",
                format="pdf",
                size_bytes=2048,
                status=DocumentStatus.READY.value,
                content_sha256="1" * 64,
                page_count=1,
                char_count=30,
                segment_count=1,
            )
            db.add(document)
            db.flush()
            db.add(
                DocumentTextSegment(
                    document_id=document.id,
                    ordinal=0,
                    page_number=1,
                    generation=1,
                    text="محتوى سري لا يجوز أن يظهر لغيره",
                    char_count=30,
                )
            )
            db.commit()
            document_id = document.id

        stranger = TestClient(app, base_url="http://testserver")
        stranger.post(
            "/api/v1/auth/register",
            json={"email": "stranger@example.com", "password": "correct-horse-battery"},
            headers={"Origin": "http://testserver"},
        )
        probe = stranger.get(f"/api/v1/documents/{document_id}/text?q=سري")
        # Same answer as a document that does not exist.
        assert probe.status_code in {403, 404}
        missing = stranger.get(f"/api/v1/documents/{uuid.uuid4()}/text?q=سري")
        assert probe.status_code == missing.status_code


class TestSignedOutRecovery:
    """Recovery must work for a user who cannot sign in at all.

    These tests use a client with **no session** — which is the real situation a
    forgotten password creates. A live run of the preview stack exposed that the
    confirm endpoint required a session, so the whole flow was impossible; the
    cookie-carrying tests above could not see it because they were already
    signed in.
    """

    def _anonymous(self, app: FastAPI) -> TestClient:
        return TestClient(app, base_url="http://testserver")

    def test_public_csrf_endpoint_issues_a_cookie_without_a_session(
        self, mail_app: FastAPI
    ) -> None:
        client = self._anonymous(mail_app)
        response = client.get("/api/v1/auth/csrf")
        assert response.status_code == 200
        assert response.json()["csrf_token"]
        # The cookie is set and readable by script, which the double submit needs.
        assert "mafhum_csrf" in response.cookies
        # And it authenticates nothing.
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_reset_flow_completes_without_any_session(
        self, mail_app: FastAPI, mail_settings: Settings
    ) -> None:
        client = self._anonymous(mail_app)
        _register(client, "recover@example.com", "forgotten-password-1")

        # Start over in a browser that has never been signed in.
        stranger = self._anonymous(mail_app)
        stranger.get("/api/v1/auth/csrf")
        csrf = stranger.cookies.get("mafhum_csrf")
        assert csrf

        request = stranger.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "recover@example.com"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert request.status_code == 202, request.text
        token = _token_from_mail(mail_settings.email_file_dir, "reset-password?token=")

        confirm = stranger.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "password": "recovered-password-2"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert confirm.status_code == 200, confirm.text

        login = self._anonymous(mail_app).post(
            "/api/v1/auth/login",
            json={"email": "recover@example.com", "password": "recovered-password-2"},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200

    def test_cross_site_origin_is_still_rejected(self, mail_app: FastAPI) -> None:
        """Removing the session requirement must not remove the origin check."""
        client = self._anonymous(mail_app)
        response = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "someone@example.com"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "csrf_failed"

    def test_missing_double_submit_token_is_rejected_when_a_cookie_exists(
        self, mail_app: FastAPI
    ) -> None:
        client = self._anonymous(mail_app)
        client.get("/api/v1/auth/csrf")
        response = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "someone@example.com"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "csrf_failed"

    def test_anonymous_request_does_not_create_a_session(self, mail_app: FastAPI) -> None:
        client = self._anonymous(mail_app)
        client.get("/api/v1/auth/csrf")
        csrf = client.cookies.get("mafhum_csrf")
        client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "nobody@example.com"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert client.get("/api/v1/auth/me").status_code == 401
        assert "mafhum_session" not in client.cookies
