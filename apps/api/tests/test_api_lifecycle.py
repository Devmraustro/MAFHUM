"""Data lifecycle: deletion, account deletion, partial failures, stale recovery."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import make_docx, make_pdf

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def table_counts(app: FastAPI) -> dict[str, int]:
    from mafhum_api.db.models import (
        Document,
        DocumentProcessingJob,
        DocumentTextSegment,
        Project,
        SourceRecord,
        User,
        UserSession,
    )

    with app.state.context.database.session() as session:
        return {
            name: int(session.scalar(select(func.count(model.id))) or 0)
            for name, model in {
                "users": User,
                "sessions": UserSession,
                "projects": Project,
                "documents": Document,
                "jobs": DocumentProcessingJob,
                "segments": DocumentTextSegment,
                "sources": SourceRecord,
            }.items()
        }


def blob_count(app: FastAPI) -> int:
    return sum(1 for p in app.state.context.storage.root.rglob("*") if p.is_file())


def make_ready_document(user: AuthedClient, app: FastAPI, kind: str = "arabic") -> str:
    mime = PDF_MIME if kind == "arabic" else DOCX_MIME
    content = make_pdf("arabic", pages=2) if kind == "arabic" else make_docx("arabic")
    document_id = user.upload(
        **upload_kwargs(content, f"{kind}.{'pdf' if kind == 'arabic' else 'docx'}", mime)
    ).json()["id"]
    run_worker(app)
    return document_id


class TestDocumentDeletion:
    def test_deletes_rows_and_blob(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = make_ready_document(user_a, app)
        assert blob_count(app) == 1
        assert table_counts(app)["segments"] > 0

        user_a.delete(f"/api/v1/documents/{document_id}")
        assert blob_count(app) == 0
        counts = table_counts(app)
        assert counts["documents"] == 0
        assert counts["segments"] == 0
        assert counts["jobs"] == 0

    def test_source_linked_to_document_survives_with_null_document(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = make_ready_document(user_a, app)
        source_id = user_a.post(
            "/api/v1/sources", json={"title": "مصدر مرتبط", "document_id": document_id}
        ).json()["id"]
        user_a.delete(f"/api/v1/documents/{document_id}")
        source = user_a.get(f"/api/v1/sources/{source_id}").json()
        assert source["document_id"] is None
        assert source["title"] == "مصدر مرتبط"

    def test_blob_deletion_failure_does_not_resurrect_row(
        self, user_a: AuthedClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the filesystem delete fails, the row must still be gone."""
        from mafhum_api.services.documents.storage import LocalFileStorage, StorageError

        document_id = make_ready_document(user_a, app)

        def broken_delete(self: Any, key: str) -> bool:
            raise StorageError("simulated filesystem failure")

        monkeypatch.setattr(LocalFileStorage, "delete", broken_delete)
        response = user_a.delete(f"/api/v1/documents/{document_id}")
        assert response.status_code == 200
        # Access is gone even though the blob remains.
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert blob_count(app) == 1

    def test_orphan_reconciliation_reclaims_failed_blob(
        self, user_a: AuthedClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mafhum_api.services.documents.storage import LocalFileStorage, StorageError

        document_id = make_ready_document(user_a, app)

        def broken_delete(self: Any, key: str) -> bool:
            raise StorageError("simulated filesystem failure")

        monkeypatch.setattr(LocalFileStorage, "delete", broken_delete)
        user_a.delete(f"/api/v1/documents/{document_id}")
        assert blob_count(app) == 1

        monkeypatch.undo()
        with app.state.context.database.session() as session:
            deleted, failed = app.state.context.documents.reconcile_orphan_blobs(session)
        assert len(deleted) == 1
        assert failed == []
        assert blob_count(app) == 0


class TestAccountDeletion:
    def test_full_account_deletion(self, user_a: AuthedClient, app: FastAPI) -> None:
        project_id = user_a.post("/api/v1/projects", json={"title": "مشروع"}).json()["id"]
        make_ready_document(user_a, app)
        user_a.post("/api/v1/sources", json={"title": "مصدر"})
        assert blob_count(app) == 1

        response = user_a.delete("/api/v1/me")
        assert response.status_code == 200
        body = response.json()
        assert body["complete"] is True
        assert body["documents_removed"] == 1
        assert body["blobs_removed"] == 1
        assert body["blobs_pending"] == 0
        assert blob_count(app) == 0

        counts = table_counts(app)
        assert counts["documents"] == 0
        assert counts["segments"] == 0
        assert counts["jobs"] == 0
        assert counts["sources"] == 0
        assert counts["projects"] == 0
        _ = project_id

    def test_access_blocked_immediately(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = make_ready_document(user_a, app)
        user_a.delete("/api/v1/me")
        assert user_a.get("/api/v1/auth/me").status_code == 401
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 401
        assert user_a.get("/api/v1/documents").status_code == 401

    def test_login_refused_after_deletion(self, user_a: AuthedClient, app: FastAPI) -> None:
        user_a.delete("/api/v1/me")
        response = user_a._client.post(
            "/api/v1/auth/login",
            json={"email": user_a.email, "password": user_a.password},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 401

    def test_other_user_unaffected(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        make_ready_document(user_a, app)
        make_ready_document(user_b, app, kind="french")
        user_a.delete("/api/v1/me")
        assert user_b.get("/api/v1/documents").json()["total"] == 1
        assert blob_count(app) == 1

    def test_partial_blob_failure_reported_honestly(
        self, user_a: AuthedClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mafhum_api.services.documents.storage import LocalFileStorage, StorageError

        make_ready_document(user_a, app)

        def broken_delete(self: Any, key: str) -> bool:
            raise StorageError("simulated filesystem failure")

        monkeypatch.setattr(LocalFileStorage, "delete", broken_delete)
        body = user_a.delete("/api/v1/me").json()
        assert body["complete"] is False
        assert body["blobs_pending"] == 1
        assert body["detail"] == {"pending": "blob_deletion_incomplete"}
        # Access is still revoked.
        assert user_a.get("/api/v1/auth/me").status_code == 401

    def test_pending_deletion_is_resumable(
        self, user_a: AuthedClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mafhum_api.services.documents.storage import LocalFileStorage, StorageError

        make_ready_document(user_a, app)

        def broken_delete(self: Any, key: str) -> bool:
            raise StorageError("simulated filesystem failure")

        monkeypatch.setattr(LocalFileStorage, "delete", broken_delete)
        user_a.delete("/api/v1/me")
        assert blob_count(app) == 1

        monkeypatch.undo()
        with app.state.context.database.session() as session:
            reports = app.state.context.deletion.resume_pending_deletions(session)
            session.commit()
        assert any(report.complete for report in reports)
        assert blob_count(app) == 0
        assert table_counts(app)["users"] == 0

    def test_account_without_documents_deletes_cleanly(self, user_a: AuthedClient) -> None:
        body = user_a.delete("/api/v1/me").json()
        assert body["complete"] is True
        assert body["documents_removed"] == 0


class TestRetryIdempotency:
    def test_retry_twice_keeps_one_segment_set(self, user_a: AuthedClient, app: FastAPI) -> None:
        from mafhum_api.db.models import Document, DocumentStatus

        document_id = uuid.UUID(make_ready_document(user_a, app))
        from tests.test_api_quotas import segment_count

        baseline = segment_count(app, document_id)
        for _ in range(3):
            with app.state.context.database.session() as session:
                document = session.get(Document, document_id)
                assert document is not None
                document.status = DocumentStatus.FAILED.value
                session.commit()
            assert user_a.post(f"/api/v1/documents/{document_id}/retry").status_code == 200
            run_worker(app)
            assert segment_count(app, document_id) == baseline

    def test_retry_generates_new_job_generation(self, user_a: AuthedClient, app: FastAPI) -> None:
        from mafhum_api.db.models import Document, DocumentProcessingJob, DocumentStatus

        document_id = uuid.UUID(make_ready_document(user_a, app))
        with app.state.context.database.session() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.status = DocumentStatus.FAILED.value
            session.commit()
        user_a.post(f"/api/v1/documents/{document_id}/retry")
        run_worker(app)
        with app.state.context.database.session() as session:
            generations = session.scalars(
                select(DocumentProcessingJob.generation)
                .where(DocumentProcessingJob.document_id == document_id)
                .order_by(DocumentProcessingJob.generation)
            ).all()
        assert list(generations) == [1, 2]


class TestStaleJobRecovery:
    def test_stale_running_job_is_requeued(self, user_a: AuthedClient, app: FastAPI) -> None:
        from datetime import datetime, timedelta

        from mafhum_api.db.models import DocumentProcessingJob, JobStatus

        document_id = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME)
        ).json()["id"]

        # Simulate a worker that died mid-job.
        with app.state.context.database.session() as session:
            job = session.scalar(select(DocumentProcessingJob).limit(1))
            assert job is not None
            job.status = JobStatus.RUNNING.value
            job.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
            session.commit()

        worker = app.state.context.worker
        with app.state.context.database.session() as session:
            recovered = worker.recover_stale_jobs(session)
            session.commit()
        assert recovered >= 1
        assert run_worker(app) >= 1
        assert user_a.get(f"/api/v1/documents/{document_id}").json()["status"] == "ready"

    def test_stale_job_exceeding_attempts_fails(self, user_a: AuthedClient, app: FastAPI) -> None:
        from datetime import datetime, timedelta

        from mafhum_api.db.models import DocumentProcessingJob, JobStatus

        document_id = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME)
        ).json()["id"]
        with app.state.context.database.session() as session:
            job = session.scalar(select(DocumentProcessingJob).limit(1))
            assert job is not None
            job.status = JobStatus.RUNNING.value
            job.attempts = 99
            job.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
            session.commit()

        with app.state.context.database.session() as session:
            app.state.context.worker.recover_stale_jobs(session)
            session.commit()
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "failed"
        assert body["error_code"] == "stale_job"

    def test_orphaned_processing_document_requeued(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        from mafhum_api.db.models import Document, DocumentStatus

        document_id = uuid.UUID(
            user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME)).json()[
                "id"
            ]
        )
        # Crash simulation: document says "processing" but no job exists.
        with app.state.context.database.session() as session:
            from mafhum_api.db.models import DocumentProcessingJob

            document = session.get(Document, document_id)
            assert document is not None
            document.status = DocumentStatus.PROCESSING.value
            for job in session.scalars(
                select(DocumentProcessingJob).where(
                    DocumentProcessingJob.document_id == document_id
                )
            ).all():
                session.delete(job)
            session.commit()

        with app.state.context.database.session() as session:
            recovered = app.state.context.worker.recover_stale_jobs(session)
            session.commit()
        assert recovered >= 1
        assert run_worker(app) >= 1
        assert user_a.get(f"/api/v1/documents/{document_id!s}").json()["status"] == "ready"


class TestTransactionRollback:
    def test_db_constraints_reject_negative_size(self, app: FastAPI) -> None:
        import io

        from sqlalchemy.exc import IntegrityError

        context = app.state.context
        from mafhum_api.services.identity.service import IdentityService

        with context.database.session() as session:
            user = IdentityService(session_ttl_minutes=60).register(
                session, email="tx@example.com", password="long-enough-password", display_name="t"
            )
            session.commit()
            owner_id = user.id

        from mafhum_api.db.models import Document

        with context.database.session() as session:
            session.add(
                Document(
                    id=uuid.uuid4(),
                    owner_id=owner_id,
                    storage_key="ab/" + "c" * 64,
                    display_filename="x.pdf",
                    format="pdf",
                    size_bytes=-1,
                    content_sha256="0" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
        _ = io

    def test_db_rejects_invalid_status(self, app: FastAPI) -> None:
        from sqlalchemy.exc import IntegrityError

        from mafhum_api.db.models import Document
        from mafhum_api.services.identity.service import IdentityService

        context = app.state.context
        with context.database.session() as session:
            user = IdentityService(session_ttl_minutes=60).register(
                session, email="tx2@example.com", password="long-enough-password", display_name="t"
            )
            session.commit()
            owner_id = user.id

        with context.database.session() as session:
            session.add(
                Document(
                    id=uuid.uuid4(),
                    owner_id=owner_id,
                    storage_key="cd/" + "e" * 64,
                    display_filename="x.pdf",
                    format="pdf",
                    size_bytes=10,
                    content_sha256="0" * 64,
                    status="not_a_status",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

    def test_duplicate_segment_ordinal_rejected(self, app: FastAPI) -> None:
        from sqlalchemy.exc import IntegrityError

        from mafhum_api.db.models import Document, DocumentTextSegment
        from mafhum_api.services.identity.service import IdentityService

        context = app.state.context
        with context.database.session() as session:
            user = IdentityService(session_ttl_minutes=60).register(
                session, email="tx3@example.com", password="long-enough-password", display_name="t"
            )
            session.commit()
            owner_id = user.id
            document_id = uuid.uuid4()
            session.add(
                Document(
                    id=document_id,
                    owner_id=owner_id,
                    storage_key="ef/" + "a" * 64,
                    display_filename="x.pdf",
                    format="pdf",
                    size_bytes=10,
                    content_sha256="0" * 64,
                )
            )
            session.commit()

        with context.database.session() as session:
            for _ in range(2):
                session.add(
                    DocumentTextSegment(
                        id=uuid.uuid4(),
                        document_id=document_id,
                        generation=1,
                        ordinal=0,
                        page_number=1,
                        text="نص",
                        char_count=3,
                    )
                )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
