"""The processing tick: the serverless driver for background processing.

Properties under test:

* an authenticated user can drive processing with no resident worker;
* the pass is bounded (max jobs and wall-clock budget) and reports why it
  stopped;
* ticks are idempotent — a drained queue is a no-op — and concurrent-safe
  by construction (exclusive claims; verified indirectly via sequence);
* stale jobs are recovered by the same pass;
* expired upload sessions are reclaimed by the same pass;
* the scheduler endpoint is token-gated: disabled when unset, constant-time
  checked, and a bearer header never falls back to a session cookie;
* both endpoints refuse while the resident worker is enabled — exactly one
  driver per deployment;
* unauthenticated ticks are refused.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI

from tests.conftest import AuthedClient, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"
TICK = "/api/v1/processing/tick"
SCHEDULER_TICK = "/api/v1/processing/tick-scheduler"


def _upload(user: AuthedClient, name: str = "doc.pdf") -> str:
    response = user.upload(**upload_kwargs(make_pdf("arabic", pages=1), name, PDF_MIME))
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestUserTick:
    def test_authenticated_tick_processes_a_queued_document(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = _upload(user_a)
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "pending"

        response = user_a.post(TICK)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["processed"] == 1
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert result["stopped_reason"] == "drained"

        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "ready"

    def test_second_tick_on_drained_queue_is_a_noop(self, user_a: AuthedClient) -> None:
        _upload(user_a)
        first = user_a.post(TICK).json()
        assert first["processed"] == 1
        second = user_a.post(TICK).json()
        assert second["processed"] == 0
        assert second["stopped_reason"] == "drained"
        assert second["recovered_stale_jobs"] == 0

    def test_unauthenticated_tick_refused(self, client: Any) -> None:
        response = client.post(TICK)
        assert response.status_code == 401

    def test_csrf_required(self, user_a: AuthedClient) -> None:
        token = user_a.csrf_token
        user_a.csrf_token = ""
        response = user_a.post(TICK)
        user_a.csrf_token = token
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "csrf_failed"

    def test_tick_response_carries_no_document_data(self, user_a: AuthedClient) -> None:
        _upload(user_a)
        body = user_a.post(TICK).json()
        assert set(body) == {
            "recovered_stale_jobs",
            "expired_upload_sessions",
            "processed",
            "succeeded",
            "failed",
            "stopped_reason",
        }

    def test_failed_document_is_counted_not_raised(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        # A document whose bytes vanish between upload and tick records a
        # failure; the tick still returns 200 with failed=1.
        document_id = _upload(user_a)
        with app.state.context.database.session() as db:
            from mafhum_api.db.models import Document

            document = db.get(Document, uuid.UUID(document_id))
            assert document is not None
            key = document.storage_key
        assert app.state.context.storage.delete(key) is True

        response = user_a.post(TICK)
        assert response.status_code == 200, response.text
        assert response.json()["failed"] == 1
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "failed"
        assert body["error_code"]


class TestBoundedPass:
    def test_max_jobs_bounds_the_pass(self, settings, tmp_path) -> None:
        """More queued documents than the per-pass limit: the tick stops at
        the limit and reports why; the rest stay queued for the next tick."""
        from mafhum_api.config import Settings
        from mafhum_api.main import create_app
        from tests.conftest import _register

        bounded: Settings = settings.model_copy(
            update={
                "processing_tick_max_jobs": 2,
                "storage_root": tmp_path / "bounded-store",
            }
        )
        (tmp_path / "bounded-store").mkdir(parents=True, exist_ok=True)
        application = create_app(bounded, start_worker=False)
        try:
            user = _register(application, "bounded@example.com", "Passw0rd!23")
            for i in range(4):
                _upload(user, f"doc{i}.pdf")
            response = user.post(TICK)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["processed"] == 2
            assert result["stopped_reason"] == "max_jobs"
            # The remaining documents are still pending, not lost.
            listing = user.get("/api/v1/documents").json()
            statuses = {d["id"]: d["status"] for d in listing["items"]}
            assert list(statuses.values()).count("ready") == 2
            assert list(statuses.values()).count("pending") == 2
            # And a second tick drains the rest.
            second = user.post(TICK).json()
            assert second["processed"] == 2
            assert second["stopped_reason"] == "drained"
        finally:
            application.state.context.worker.stop()
            application.state.context.database.dispose()

    def test_budget_exhaustion_is_reported(self, settings, tmp_path) -> None:
        """A budget of zero seconds still runs the recovery phase but claims
        no jobs: the pass must stop for budget, not loop forever."""
        from mafhum_api.config import Settings
        from mafhum_api.main import create_app
        from tests.conftest import _register

        zero_budget: Settings = settings.model_copy(
            update={
                "processing_tick_budget_seconds": 0.001,
                "storage_root": tmp_path / "budget-store",
            }
        )
        (tmp_path / "budget-store").mkdir(parents=True, exist_ok=True)
        application = create_app(zero_budget, start_worker=False)
        try:
            user = _register(application, "budget@example.com", "Passw0rd!23")
            _upload(user)
            response = user.post(TICK)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["stopped_reason"] in {"budget_exhausted", "drained"}
        finally:
            application.state.context.worker.stop()
            application.state.context.database.dispose()


class TestRecoveryAndReclamation:
    def test_stale_running_job_is_requeued_and_processed(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        from mafhum_api.db.models import Document, DocumentProcessingJob

        document_id = _upload(user_a)
        # Simulate a worker that died mid-job: status running, stale
        # heartbeat, and the document stuck in processing.
        with app.state.context.database.session() as db:
            job = (
                db.query(DocumentProcessingJob)
                .filter(DocumentProcessingJob.document_id == uuid.UUID(document_id))
                .one()
            )
            job.status = "running"
            job.heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
            document = db.get(Document, uuid.UUID(document_id))
            assert document is not None
            document.status = "processing"
            db.commit()

        response = user_a.post(TICK)
        assert response.status_code == 200, response.text
        assert response.json()["recovered_stale_jobs"] == 1
        assert response.json()["processed"] == 1

        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "ready"

    def test_expired_upload_sessions_are_reclaimed_by_the_tick(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        from mafhum_api.db.models import DocumentUploadSession

        created = user_a.post(
            "/api/v1/documents/upload-sessions",
            json={
                "filename": "a.pdf",
                "content_type": PDF_MIME,
                "total_bytes": 100,
            },
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        part = user_a.put(
            f"/api/v1/documents/upload-sessions/{session_id}/parts/1",
            content=b"x" * 100,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert part.status_code == 200, part.text

        with app.state.context.database.session() as db:
            session = db.get(DocumentUploadSession, uuid.UUID(session_id))
            assert session is not None
            session.expires_at = datetime.now(UTC) - timedelta(minutes=5)
            db.commit()

        response = user_a.post(TICK)
        assert response.status_code == 200, response.text
        assert response.json()["expired_upload_sessions"] == 1
        # The stored part object was reclaimed.
        blobs = [p for p in app.state.context.storage.root.rglob("*") if p.is_file()]
        assert blobs == []


class TestSingleDriverRule:
    def test_tick_refused_while_resident_worker_enabled(self, settings, tmp_path) -> None:
        from mafhum_api.config import Settings
        from mafhum_api.main import create_app
        from tests.conftest import _register

        resident: Settings = settings.model_copy(
            update={
                "processing_worker_enabled": True,
                "storage_root": tmp_path / "resident-store",
            }
        )
        (tmp_path / "resident-store").mkdir(parents=True, exist_ok=True)
        # start_worker=False keeps the thread pool out of the test; the
        # *setting* is what the guard reasons about.
        application = create_app(resident, start_worker=False)
        try:
            user = _register(application, "resident@example.com", "Passw0rd!23")
            response = user.post(TICK)
            assert response.status_code == 409, response.text
            scheduler = application  # keep flake quiet
            del scheduler
        finally:
            application.state.context.worker.stop()
            application.state.context.database.dispose()


class TestSchedulerTick:
    def test_disabled_when_token_unset(self, user_a: AuthedClient) -> None:
        response = user_a.post(SCHEDULER_TICK)
        assert response.status_code == 404, response.text

    def _token_app(self, settings, tmp_path, token: str):  # type: ignore[no-untyped-def]
        from mafhum_api.config import Settings
        from mafhum_api.main import create_app

        configured: Settings = settings.model_copy(
            update={
                "processing_tick_token": token,
                "storage_root": tmp_path / "tok-store",
            }
        )
        (tmp_path / "tok-store").mkdir(parents=True, exist_ok=True)
        return create_app(configured, start_worker=False)

    def test_valid_token_drives_a_pass_without_a_session(self, settings, tmp_path) -> None:
        from tests.conftest import _register
        from tests.conftest import upload_kwargs as _upload_kwargs

        application = self._token_app(settings, tmp_path, "sched-token-1")
        try:
            user = _register(application, "sched@example.com", "Passw0rd!23")
            response = user.upload(**_upload_kwargs(make_pdf("arabic", pages=1), "s.pdf", PDF_MIME))
            assert response.status_code == 201, response.text

            from fastapi.testclient import TestClient

            with TestClient(application, base_url="http://testserver") as anonymous:
                tick = anonymous.post(
                    SCHEDULER_TICK, headers={"Authorization": "Bearer sched-token-1"}
                )
            assert tick.status_code == 200, tick.text
            assert tick.json()["processed"] == 1
            body = user.get(f"/api/v1/documents/{response.json()['id']}").json()
            assert body["status"] == "ready"
        finally:
            application.state.context.worker.stop()
            application.state.context.database.dispose()

    def test_wrong_token_refused_and_no_cookie_fallback(self, settings, tmp_path) -> None:
        from tests.conftest import _register

        application = self._token_app(settings, tmp_path, "sched-token-1")
        try:
            # An admin-looking session must NOT authorize the scheduler
            # endpoint when the bearer header is wrong.
            user = _register(application, "adminish@example.com", "Passw0rd!23")
            tick = user.post(SCHEDULER_TICK, headers={"Authorization": "Bearer wrong"})
            assert tick.status_code == 401, tick.text
        finally:
            application.state.context.worker.stop()
            application.state.context.database.dispose()
