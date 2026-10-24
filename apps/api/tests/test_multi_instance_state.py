"""Correctness across multiple API instances sharing one database.

ADR 0004 states that "quota enforcement is process-local" and that the supported
topology is one API process. This module tests that claim instead of trusting it.

Two independent application instances are built over the same PostgreSQL
database, which is what two replicas behind a load balancer look like from the
database's point of view. Anything that is genuinely shared must hold across
both; anything process-local will not.

The results recorded here are what the code actually does, not what the ADR
claims -- where they disagree, the ADR is corrected rather than the test bent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from mafhum_api.config import Settings
from mafhum_api.main import create_app
from tests.conftest import AuthedClient, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"


@pytest.fixture
def second_app(settings: Settings, tmp_path: Path) -> Iterator[FastAPI]:
    """A second, fully independent application instance.

    It gets its own ``AppContext``, engine pool, service objects and worker -- the
    only thing shared with the primary ``app`` fixture is the database, which is
    exactly the sharing a second replica would have.
    """
    replica_settings = settings.model_copy(update={"storage_root": tmp_path / "storage-replica"})
    Path(replica_settings.storage_root).mkdir(parents=True, exist_ok=True)
    application = create_app(replica_settings, start_worker=False)
    yield application
    application.state.context.worker.stop()
    application.state.context.database.dispose()


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _register_on(app: FastAPI, email: str, password: str) -> AuthedClient:
    """Create the account on this instance."""
    client = _client(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text
    return _finish(client, email, password, response.json()["user"]["id"])


def _login_on(app: FastAPI, email: str, password: str) -> AuthedClient:
    """Sign the *same* user into a second instance.

    This is what a second replica looks like: the account already exists in the
    shared database, so the replica must authenticate against it rather than
    create it. A 409 here would mean the replica cannot see the primary's writes.
    """
    client = _client(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    return _finish(client, email, password, response.json()["user"]["id"])


def _finish(client: TestClient, email: str, password: str, user_id: str) -> AuthedClient:
    authed = AuthedClient(client)
    authed.email = email
    authed.password = password
    authed.user_id = uuid.UUID(user_id)
    # The CSRF token is not in the auth body; the browser fetches it from the
    # session endpoint, so the test does the same.
    authed.refresh_csrf()
    return authed


class TestQuotaAcrossInstances:
    def test_document_count_quota_holds_across_two_instances(
        self, app: FastAPI, second_app: FastAPI, clean_tables: None
    ) -> None:
        """The per-user document cap is enforced database-wide, not per process.

        The limit is 5. Uploads are split across the two instances; if the cap
        were process-local, each would allow 5 and the total would reach 10.
        """
        user = _register_on(app, "quota-a@example.com", "Passw0rd!23")
        replica = _login_on(second_app, "quota-a@example.com", "Passw0rd!23")
        # The replica sees the same account, not a second one.
        assert replica.user_id == user.user_id

        limit = app.state.context.settings.max_documents_per_user
        assert limit == 5

        accepted = 0
        for index in range(limit + 4):
            target = user if index % 2 == 0 else replica
            content = make_pdf("arabic", pages=1)
            response = target.post(
                "/api/v1/documents",
                **upload_kwargs(content, f"doc-{index}.pdf", PDF_MIME),
            )
            if response.status_code in (200, 201):
                accepted += 1
            else:
                assert response.status_code == 429 or response.json()["code"] == "quota_exceeded", (
                    response.text
                )

        assert accepted == limit, (
            f"expected exactly {limit} documents accepted across both instances, "
            f"got {accepted} -- a per-process quota would allow roughly twice that"
        )

    def test_byte_quota_holds_across_two_instances(
        self, app: FastAPI, second_app: FastAPI, clean_tables: None
    ) -> None:
        """The per-user byte cap is enforced database-wide too."""
        user = _register_on(app, "quota-b@example.com", "Passw0rd!23")
        replica = _login_on(second_app, "quota-b@example.com", "Passw0rd!23")

        settings = app.state.context.settings
        byte_cap = settings.max_total_bytes_per_user_mb * 1024 * 1024
        content = make_pdf("arabic", pages=1)
        size = len(content)

        accepted_bytes = 0
        for index in range(400):
            target = user if index % 2 == 0 else replica
            response = target.post(
                "/api/v1/documents",
                **upload_kwargs(content, f"byte-{index}.pdf", PDF_MIME),
            )
            if response.status_code in (200, 201):
                accepted_bytes += size
            else:
                break
        # Whichever cap bites first, the total must not exceed the byte cap by
        # more than one file's worth (the file that hit the boundary).
        assert accepted_bytes <= byte_cap + size, (
            f"accepted {accepted_bytes} bytes against a cap of {byte_cap}"
        )
        assert accepted_bytes > 0, "no upload succeeded at all"

    def test_storage_usage_agrees_across_instances(
        self, app: FastAPI, second_app: FastAPI, clean_tables: None
    ) -> None:
        """Both instances read the same usage figures from the database."""
        user = _register_on(app, "quota-c@example.com", "Passw0rd!23")
        content = make_pdf("arabic", pages=1)
        response = user.post("/api/v1/documents", **upload_kwargs(content, "one.pdf", PDF_MIME))
        assert response.status_code in (200, 201), response.text

        owner_id = user.user_id
        with app.state.context.database.session() as primary:
            a_count, a_bytes = app.state.context.documents.storage_usage(primary, owner_id)
        with second_app.state.context.database.session() as replica:
            b_count, b_bytes = second_app.state.context.documents.storage_usage(replica, owner_id)
        assert (a_count, a_bytes) == (b_count, b_bytes) == (1, len(content))


class TestLoginThrottleAcrossInstances:
    """The throttle is the one control that used to be process-local.

    It counted attempts in a module-level dictionary, so behind N replicas an
    attacker got roughly N times the configured budget. These tests drive two
    independent application instances over one database and require the
    *configured* limit to be the real limit.
    """

    def _login(self, app: FastAPI, email: str, password: str) -> int:
        return (
            _client(app)
            .post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
                headers={"Origin": "http://testserver"},
            )
            .status_code
        )

    def test_attempts_are_shared_between_instances(
        self, app: FastAPI, second_app: FastAPI, clean_tables: None
    ) -> None:
        """Splitting attempts across two instances must not double the budget."""
        _register_on(app, "throttle@example.com", "Passw0rd!23")

        limit = app.state.context.settings.login_throttle_max_attempts
        assert limit == 10

        blocked = 0
        for index in range(limit + 6):
            target = app if index % 2 == 0 else second_app
            # Wrong password every time: these are the attempts that must count.
            if self._login(target, "throttle@example.com", "wrong-password") == 429:
                blocked += 1

        assert blocked > 0, (
            "no attempt was ever throttled -- a per-process counter would let each "
            "instance serve the full budget, so the limit is not really enforced"
        )
        # The first `limit` attempts are allowed through to authentication; every
        # attempt after that must be refused before the password is even checked.
        assert blocked == 6, f"expected exactly 6 blocked attempts, got {blocked}"

    def test_a_blocked_client_cannot_reach_authentication(
        self, app: FastAPI, second_app: FastAPI, clean_tables: None
    ) -> None:
        """Once throttled, the correct password is refused too.

        This is the point of the control: it stops credential guessing, so a
        blocked client must not keep getting password-checking service.
        """
        _register_on(app, "locked@example.com", "Passw0rd!23")
        limit = app.state.context.settings.login_throttle_max_attempts

        for index in range(limit):
            target = app if index % 2 == 0 else second_app
            assert self._login(target, "locked@example.com", "wrong-password") == 401

        # The budget is now spent across both instances combined.
        assert self._login(app, "locked@example.com", "Passw0rd!23") == 429
        assert self._login(second_app, "locked@example.com", "Passw0rd!23") == 429

    def test_a_successful_login_is_recorded_as_such(self, app: FastAPI, clean_tables: None) -> None:
        """The outcome is stored, so a throttle can be told from a confused user."""
        from sqlalchemy import select

        from mafhum_api.db.models import LoginAttempt

        user = _register_on(app, "recorded@example.com", "Passw0rd!23")
        self._login(app, "recorded@example.com", "wrong-password")
        assert self._login(app, "recorded@example.com", "Passw0rd!23") == 200
        assert user.user_id is not None

        with app.state.context.database.session() as db:
            rows = list(db.scalars(select(LoginAttempt)).all())
        assert len(rows) >= 2
        assert any(row.succeeded for row in rows)
        assert any(not row.succeeded for row in rows)

    def test_client_addresses_are_not_stored_in_plaintext(
        self, app: FastAPI, clean_tables: None
    ) -> None:
        """The stored key is keyed-hashed; a table dump does not yield addresses."""
        from sqlalchemy import select

        from mafhum_api.db.models import LoginAttempt

        _register_on(app, "hashed@example.com", "Passw0rd!23")
        self._login(app, "hashed@example.com", "wrong-password")

        with app.state.context.database.session() as db:
            keys = list(db.scalars(select(LoginAttempt.client_key)).all())
        assert keys, "no attempt was recorded"
        for key in keys:
            assert key != "testclient", "the raw client key was stored"
            assert len(key) == 64, "expected a sha256 hex digest"

    def test_expired_attempts_are_pruned(self, app: FastAPI, clean_tables: None) -> None:
        """Rows outside the window are removed, so the table stays bounded.

        The dictionary this replaced never evicted anything and grew for the life
        of the process.
        """
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from mafhum_api.db.models import LoginAttempt

        throttle = app.state.context.login_throttle
        old = datetime.now(UTC) - timedelta(hours=2)
        with app.state.context.database.session() as db:
            for index in range(5):
                db.add(
                    LoginAttempt(
                        client_key=throttle.hash_key(f"203.0.113.{index}"),
                        attempted_at=old,
                        succeeded=False,
                    )
                )
            db.commit()

        removed = throttle.prune(older_than_seconds=3600)
        assert removed == 5
        with app.state.context.database.session() as db:
            assert db.scalar(select(LoginAttempt.id)) is None

    def test_recording_an_attempt_prunes_that_keys_own_history(
        self, app: FastAPI, clean_tables: None
    ) -> None:
        """The request path prunes without needing a separate job."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import func, select

        from mafhum_api.db.models import LoginAttempt

        throttle = app.state.context.login_throttle
        old = datetime.now(UTC) - timedelta(hours=2)
        with app.state.context.database.session() as db:
            for _ in range(3):
                db.add(
                    LoginAttempt(
                        client_key=throttle.hash_key("198.51.100.9"),
                        attempted_at=old,
                        succeeded=False,
                    )
                )
            db.commit()

        throttle.record("198.51.100.9", succeeded=False)

        with app.state.context.database.session() as db:
            count = db.scalar(
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.client_key == throttle.hash_key("198.51.100.9")
                )
            )
        assert int(count or 0) == 1, "the expired rows were not pruned on write"
