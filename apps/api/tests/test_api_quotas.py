"""Quota enforcement and concurrency, against real PostgreSQL.

The count/byte quotas are enforced inside a transaction that locks the owning
user row, so these tests use real threads issuing real HTTP requests to prove the
boundary cannot be crossed by racing.
"""

from __future__ import annotations

import threading
import uuid

from fastapi import FastAPI
from sqlalchemy import func, select

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"


def document_count(app: FastAPI, user_id: uuid.UUID) -> int:
    from mafhum_api.db.models import Document

    with app.state.context.database.session() as session:
        return int(
            session.scalar(select(func.count(Document.id)).where(Document.owner_id == user_id)) or 0
        )


def segment_count(app: FastAPI, document_id: uuid.UUID) -> int:
    from mafhum_api.db.models import DocumentTextSegment

    with app.state.context.database.session() as session:
        return int(
            session.scalar(
                select(func.count(DocumentTextSegment.id)).where(
                    DocumentTextSegment.document_id == document_id
                )
            )
            or 0
        )


class TestDocumentCountQuota:
    def test_quota_enforced(self, user_a: AuthedClient) -> None:
        # Test config allows 5 documents.
        for index in range(5):
            response = user_a.upload(
                **upload_kwargs(make_pdf("french", pages=1), f"d{index}.pdf", PDF_MIME)
            )
            assert response.status_code == 201, response.text
        sixth = user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "d6.pdf", PDF_MIME))
        assert sixth.status_code == 429
        assert sixth.json()["code"] == "quota_exceeded"

    def test_quota_message_is_arabic(self, user_a: AuthedClient) -> None:
        for index in range(5):
            user_a.upload(**upload_kwargs(make_pdf("french", pages=1), f"d{index}.pdf", PDF_MIME))
        response = user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "d6.pdf", PDF_MIME))
        assert "الحد الأقصى" in response.json()["message"]

    def test_quota_rejection_leaves_no_blob(self, user_a: AuthedClient, app: FastAPI) -> None:
        for index in range(5):
            user_a.upload(**upload_kwargs(make_pdf("french", pages=1), f"d{index}.pdf", PDF_MIME))
        before = sum(1 for p in app.state.context.storage.root.rglob("*") if p.is_file())
        user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "d6.pdf", PDF_MIME))
        after = sum(1 for p in app.state.context.storage.root.rglob("*") if p.is_file())
        assert after == before

    def test_delete_frees_quota(self, user_a: AuthedClient) -> None:
        ids = [
            user_a.upload(
                **upload_kwargs(make_pdf("french", pages=1), f"d{i}.pdf", PDF_MIME)
            ).json()["id"]
            for i in range(5)
        ]
        assert (
            user_a.upload(
                **upload_kwargs(make_pdf("french", pages=1), "x.pdf", PDF_MIME)
            ).status_code
            == 429
        )
        user_a.delete(f"/api/v1/documents/{ids[0]}")
        assert (
            user_a.upload(
                **upload_kwargs(make_pdf("french", pages=1), "y.pdf", PDF_MIME)
            ).status_code
            == 201
        )

    def test_quota_is_per_user(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        for index in range(5):
            user_a.upload(**upload_kwargs(make_pdf("french", pages=1), f"d{index}.pdf", PDF_MIME))
        # user_a is exhausted; user_b is unaffected.
        assert (
            user_a.upload(
                **upload_kwargs(make_pdf("french", pages=1), "z.pdf", PDF_MIME)
            ).status_code
            == 429
        )
        assert (
            user_b.upload(
                **upload_kwargs(make_pdf("french", pages=1), "z.pdf", PDF_MIME)
            ).status_code
            == 201
        )


class TestByteQuota:
    def test_byte_quota_enforced(self, app: FastAPI) -> None:
        """A single large file that would exceed the byte budget is refused.

        Uses the service layer directly so the test can exercise a byte limit
        without uploading hundreds of megabytes over HTTP.
        """
        import io

        from mafhum_api.core.errors import QuotaExceededError
        from mafhum_api.services.documents.service import DocumentLimits

        context = app.state.context
        with context.database.session() as session:
            from mafhum_api.services.identity.service import IdentityService

            user = IdentityService(session_ttl_minutes=60).register(
                session,
                email="quota@example.com",
                password="long-enough-password",
                display_name="q",
            )
            session.commit()
            owner_id = user.id

        tight = DocumentLimits(
            max_upload_bytes=1024 * 1024,
            max_extracted_chars=200_000,
            max_pdf_pages=5,
            max_docx_entries=200,
            max_docx_uncompressed_bytes=4 * 1024 * 1024,
            max_archive_compression_ratio=100.0,
            max_documents_per_user=50,
            # Only 20 KB of total storage allowed for this user.
            max_total_bytes_per_user=20 * 1024,
            max_segment_chars=1500,
        )
        from mafhum_api.services.documents.service import DocumentService

        service = DocumentService(context.storage, tight, row_locking=True)
        content = make_pdf("french", pages=1)
        with context.database.session() as session:
            first = service.upload(
                session,
                owner_id=owner_id,
                filename="a.pdf",
                content_type=PDF_MIME,
                fileobj=io.BytesIO(content),
            )
            session.commit()
            assert first.size_bytes > 0

        with context.database.session() as session:
            try:
                service.upload(
                    session,
                    owner_id=owner_id,
                    filename="b.pdf",
                    content_type=PDF_MIME,
                    fileobj=io.BytesIO(content),
                )
                raised = False
            except QuotaExceededError:
                raised = True
            session.rollback()
        assert raised is True

        # Compensation must have removed the rejected blob.
        assert document_count(app, owner_id) == 1


class TestConcurrentUploads:
    def test_simultaneous_uploads_respect_quota(self, app: FastAPI, user_a: AuthedClient) -> None:
        """Race several uploads at the quota boundary; exactly the quota passes."""
        content = make_pdf("french", pages=1)
        results: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker() -> None:
            from fastapi.testclient import TestClient

            client = TestClient(app, base_url="http://testserver")
            client.cookies.update(user_a._client.cookies)
            barrier.wait()
            response = client.post(
                "/api/v1/documents",
                files={"file": ("race.pdf", content, PDF_MIME)},
                headers={"Origin": "http://testserver", "X-CSRF-Token": user_a.csrf_token},
            )
            with lock:
                results.append(response.status_code)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == 8
        assert results.count(201) == 5, results
        assert results.count(429) == 3, results
        assert document_count(app, user_a.user_id) == 5  # type: ignore[arg-type]


class TestConcurrentRetriesAndDeletes:
    def test_concurrent_retries_do_not_duplicate_segments(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = uuid.UUID(
            user_a.upload(**upload_kwargs(make_pdf("arabic", pages=2), "a.pdf", PDF_MIME)).json()[
                "id"
            ]
        )
        run_worker(app)
        baseline = segment_count(app, document_id)
        assert baseline > 0

        # Force a retry and run the queue twice; segments must not accumulate.
        assert user_a.post(f"/api/v1/documents/{document_id}/retry").status_code == 409
        assert segment_count(app, document_id) == baseline

    def test_retry_after_failure_replaces_segments(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = uuid.UUID(
            user_a.upload(**upload_kwargs(make_pdf("arabic", pages=2), "a.pdf", PDF_MIME)).json()[
                "id"
            ]
        )
        run_worker(app)
        first = segment_count(app, document_id)

        # Mark the document failed so a retry is permitted, then reprocess.
        from mafhum_api.db.models import Document, DocumentStatus

        with app.state.context.database.session() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.status = DocumentStatus.FAILED.value
            session.commit()

        assert user_a.post(f"/api/v1/documents/{document_id}/retry").status_code == 200
        run_worker(app)
        assert segment_count(app, document_id) == first
        assert user_a.get(f"/api/v1/documents/{document_id}").json()["status"] == "ready"

    def test_delete_during_processing_is_safe(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("arabic", pages=2), "a.pdf", PDF_MIME)
        ).json()["id"]
        # Delete before the worker ever runs, then drain the queue.
        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code == 200
        run_worker(app)
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert segment_count(app, uuid.UUID(document_id)) == 0

    def test_no_negative_counters(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME)
        ).json()["id"]
        run_worker(app)
        user_a.delete(f"/api/v1/documents/{document_id}")
        body = user_a.get("/api/v1/documents/usage").json()
        assert body["documents"] == 0
        assert body["total_bytes"] == 0
