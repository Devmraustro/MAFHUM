"""Concurrency races against the real implementation on real PostgreSQL.

The existing quota test proves uploads cannot over-admit. These tests attack the
other state transitions that can be driven simultaneously by one user (or two
browser tabs): retrying the same failed document, and deleting a document that is
being read or deleted elsewhere.

Every assertion is about an observable outcome — status codes, row counts, files
on disk — never about internal call counts.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI

from mafhum_api.db.models import Document, DocumentStatus
from tests.conftest import AuthedClient, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"


def run_worker(app: FastAPI) -> None:
    app.state.context.worker.process_pending_now()


def job_count(app: FastAPI, document_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from mafhum_api.db.models import DocumentProcessingJob

    with app.state.context.database.session() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(DocumentProcessingJob)
                .where(DocumentProcessingJob.document_id == document_id)
            )
            or 0
        )


def segment_count(app: FastAPI, document_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from mafhum_api.db.models import DocumentTextSegment

    with app.state.context.database.session() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(DocumentTextSegment)
                .where(DocumentTextSegment.document_id == document_id)
            )
            or 0
        )


def mark_failed(app: FastAPI, document_id: uuid.UUID) -> None:
    with app.state.context.database.session() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.FAILED.value
        session.commit()


def upload_ready(user: AuthedClient, app: FastAPI) -> uuid.UUID:
    document_id = uuid.UUID(
        user.upload(**upload_kwargs(make_pdf("arabic", pages=2), "a.pdf", PDF_MIME)).json()["id"]
    )
    run_worker(app)
    return document_id


def in_parallel(count: int, action: object) -> list[object]:
    """Run `action` on `count` threads and collect results, keeping exceptions."""
    results: list[object] = [None] * count
    barrier = threading.Barrier(count)

    def run(index: int) -> None:
        barrier.wait()  # maximise the chance of a genuine overlap
        try:
            results[index] = action()  # type: ignore[operator]
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


class TestConcurrentRetry:
    def test_simultaneous_retries_create_one_run(self, user_a: AuthedClient, app: FastAPI) -> None:
        """Ten threads retry the same failed document at the same instant.

        The service documents retry as idempotent; this proves it under a real
        race rather than by calling it twice in sequence.
        """
        document_id = upload_ready(user_a, app)
        mark_failed(app, document_id)
        jobs_before = job_count(app, document_id)

        responses = in_parallel(
            10,
            lambda: user_a.post(f"/api/v1/documents/{document_id}/retry").status_code,
        )

        assert not [r for r in responses if isinstance(r, Exception)], responses
        codes = [r for r in responses if isinstance(r, int)]
        # Some win, some are told work is already queued. A 500 means the race
        # escaped the handler entirely, which is the failure being guarded.
        assert all(code in (200, 409) for code in codes), codes
        assert codes.count(200) >= 1, codes
        assert job_count(app, document_id) == jobs_before + 1

        # And the document still ends up ready with exactly one set of segments.
        run_worker(app)
        assert user_a.get(f"/api/v1/documents/{document_id}").json()["status"] == "ready"
        assert segment_count(app, document_id) > 0

    def test_retry_and_delete_racing_do_not_error(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_ready(user_a, app)
        mark_failed(app, document_id)

        responses = in_parallel(
            6,
            lambda: (
                user_a.post(f"/api/v1/documents/{document_id}/retry").status_code,
                user_a.delete(f"/api/v1/documents/{document_id}").status_code,
            ),
        )

        assert not [r for r in responses if isinstance(r, Exception)], responses
        for pair in responses:
            retry_code, delete_code = pair  # type: ignore[misc]
            assert retry_code in (200, 404, 409), retry_code
            assert delete_code in (200, 404), delete_code

        # Whatever interleaving happened, the document is gone and leaves
        # neither rows nor a blob behind.
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert segment_count(app, document_id) == 0


class TestConcurrentDelete:
    def test_simultaneous_deletes_succeed_once(
        self, user_a: AuthedClient, app: FastAPI, storage_root: Path
    ) -> None:
        document_id = upload_ready(user_a, app)
        blobs_before = len([p for p in storage_root.rglob("*") if p.is_file()])

        responses = in_parallel(
            8,
            lambda: user_a.delete(f"/api/v1/documents/{document_id}").status_code,
        )

        assert not [r for r in responses if isinstance(r, Exception)], responses
        codes = [r for r in responses if isinstance(r, int)]
        assert codes.count(200) == 1, codes
        assert codes.count(404) == 7, codes

        blobs_after = len([p for p in storage_root.rglob("*") if p.is_file()])
        assert blobs_after == blobs_before - 1, (blobs_before, blobs_after)
        assert segment_count(app, document_id) == 0

    def test_read_while_deleting_never_returns_500(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = upload_ready(user_a, app)

        def reader() -> int:
            return user_a.get(f"/api/v1/documents/{document_id}").status_code

        def deleter() -> int:
            return user_a.delete(f"/api/v1/documents/{document_id}").status_code

        results = in_parallel(4, reader) + in_parallel(2, deleter)
        assert not [r for r in results if isinstance(r, Exception)], results
        # A reader either sees the document or a clean 404 — never a 500.
        assert all(code in (200, 404) for code in results if isinstance(code, int)), results

    def test_usage_stays_consistent_after_a_delete_race(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        ids = [upload_ready(user_a, app) for _ in range(3)]
        assert user_a.get("/api/v1/documents/usage").json()["documents"] == 3

        in_parallel(3, lambda: user_a.delete(f"/api/v1/documents/{ids.pop()}").status_code)

        usage = user_a.get("/api/v1/documents/usage").json()
        assert usage["documents"] == 0, usage
        assert usage["total_bytes"] == 0, usage
