"""Document endpoint tests: the full upload -> process -> read -> delete slice."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import (
    ARABIC_PARAGRAPHS,
    ARABIC_TITLE,
    FRENCH_PARAGRAPHS,
    make_docx,
    make_pdf,
)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload_pdf(user: AuthedClient, kind: str = "arabic", pages: int = 2, **kw: Any) -> Any:
    return user.upload(**upload_kwargs(make_pdf(kind, pages=pages), f"{kind}.pdf", PDF_MIME), **kw)


def upload_docx(user: AuthedClient, kind: str = "arabic", **kw: Any) -> Any:
    return user.upload(**upload_kwargs(make_docx(kind), f"{kind}.docx", DOCX_MIME), **kw)


class TestUpload:
    def test_upload_pdf_accepted(self, user_a: AuthedClient) -> None:
        response = upload_pdf(user_a)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["format"] == "pdf"
        assert body["status"] == "pending"
        assert body["size_bytes"] > 0
        assert body["display_filename"] == "arabic.pdf"

    def test_upload_docx_accepted(self, user_a: AuthedClient) -> None:
        response = upload_docx(user_a)
        assert response.status_code == 201, response.text
        assert response.json()["format"] == "docx"

    def test_upload_requires_authentication(self, client) -> None:  # type: ignore[no-untyped-def]
        response = client.post(
            "/api/v1/documents",
            files={"file": ("a.pdf", make_pdf("french", pages=1), PDF_MIME)},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 401

    def test_arabic_filename_preserved(self, user_a: AuthedClient) -> None:
        response = user_a.upload(
            **upload_kwargs(make_pdf("arabic", pages=1), "بحث_التخرج.pdf", PDF_MIME)
        )
        assert response.status_code == 201
        assert response.json()["display_filename"] == "بحث_التخرج.pdf"

    def test_response_never_exposes_storage_path(self, user_a: AuthedClient, app: FastAPI) -> None:
        response = upload_pdf(user_a)
        storage_root = str(app.state.context.storage.root)
        body = response.json()
        assert "storage_key" not in body
        assert storage_root not in response.text
        assert "/data/" not in response.text

    def test_limits_endpoint_reports_server_config(self, user_a: AuthedClient) -> None:
        response = user_a.get("/api/v1/documents/limits")
        assert response.status_code == 200
        body = response.json()
        assert body["allowed_extensions"] == [".docx", ".pdf"]
        assert body["ocr_available"] is False
        assert body["max_upload_mb"] >= 1

    def test_upload_without_project_allowed(self, user_a: AuthedClient) -> None:
        response = upload_pdf(user_a)
        assert response.status_code == 201
        assert response.json()["project_id"] is None

    def test_upload_with_own_project(self, user_a: AuthedClient) -> None:
        project_id = user_a.post("/api/v1/projects", json={"title": "مشروعي"}).json()["id"]
        response = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME, project_id)
        )
        assert response.status_code == 201
        assert response.json()["project_id"] == project_id

    def test_upload_with_foreign_project_rejected(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        project_id = user_b.post("/api/v1/projects", json={"title": "مشروع ب"}).json()["id"]
        response = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME, project_id)
        )
        assert response.status_code == 422
        assert response.json()["field_errors"]["project_id"] == ["غير متاح."]

    def test_upload_with_unknown_project_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME, str(uuid.uuid4()))
        )
        assert response.status_code == 422

    def test_upload_with_malformed_project_id_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME, "not-a-uuid")
        )
        assert response.status_code == 422


class TestProcessing:
    def test_pdf_reaches_ready_with_arabic_text(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "arabic", pages=2).json()["id"]
        assert run_worker(app) >= 1
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "ready"
        assert body["page_count"] == 2
        assert body["char_count"] > 0
        assert body["segment_count"] > 0
        assert body["language_hint"] == "ar"
        assert body["parser_name"] == "pypdf"
        assert body["parser_version"]
        assert body["processed_at"] is not None
        assert body["error_code"] is None

    def test_docx_reaches_ready(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_docx(user_a, "french").json()["id"]
        run_worker(app)
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "ready"
        assert body["language_hint"] == "fr"
        assert body["parser_name"] == "python-docx"

    def test_segments_preserve_arabic_content(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "arabic", pages=1).json()["id"]
        run_worker(app)
        items = user_a.get(f"/api/v1/documents/{document_id}/text").json()["items"]
        joined = " ".join(item["text"] for item in items)
        assert ARABIC_TITLE in joined
        assert ARABIC_PARAGRAPHS[0] in joined

    def test_french_content_preserved(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        run_worker(app)
        items = user_a.get(f"/api/v1/documents/{document_id}/text").json()["items"]
        joined = " ".join(item["text"] for item in items)
        assert FRENCH_PARAGRAPHS[0] in joined

    def test_text_before_processing_is_empty(self, user_a: AuthedClient) -> None:
        document_id = upload_pdf(user_a).json()["id"]
        response = user_a.get(f"/api/v1/documents/{document_id}/text")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["status"] == "pending"

    def test_text_pagination(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "arabic", pages=2).json()["id"]
        run_worker(app)
        first = user_a.get(f"/api/v1/documents/{document_id}/text?page=1&page_size=2").json()
        second = user_a.get(f"/api/v1/documents/{document_id}/text?page=2&page_size=2").json()
        assert len(first["items"]) == 2
        assert len(second["items"]) == 2
        assert first["items"][0]["ordinal"] == 0
        assert second["items"][0]["ordinal"] == 2
        assert first["total"] == second["total"]

    def test_page_size_out_of_range_rejected(self, user_a: AuthedClient) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert user_a.get(f"/api/v1/documents/{document_id}/text?page_size=5000").status_code == 422

    def test_segments_record_page_numbers(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "arabic", pages=2).json()["id"]
        run_worker(app)
        items = user_a.get(f"/api/v1/documents/{document_id}/text?page_size=100").json()["items"]
        pages = {item["page_number"] for item in items}
        assert 1 in pages
        assert 2 in pages


class TestListAndRead:
    def test_list_own_documents(self, user_a: AuthedClient) -> None:
        upload_pdf(user_a, "arabic", pages=1)
        upload_docx(user_a, "french")
        body = user_a.get("/api/v1/documents").json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_list_requires_authentication(self, client) -> None:  # type: ignore[no-untyped-def]
        assert client.get("/api/v1/documents").status_code == 401
        assert client.get("/api/v1/documents/usage").status_code == 401

    def test_list_filter_by_project(self, user_a: AuthedClient) -> None:
        project_id = user_a.post("/api/v1/projects", json={"title": "مشروع"}).json()["id"]
        user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "in.pdf", PDF_MIME, project_id))
        user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "out.pdf", PDF_MIME))
        scoped = user_a.get(f"/api/v1/documents?project_id={project_id}").json()
        assert scoped["total"] == 1
        assert scoped["items"][0]["display_filename"] == "in.pdf"

    def test_list_pagination(self, user_a: AuthedClient) -> None:
        for index in range(3):
            user_a.upload(**upload_kwargs(make_pdf("french", pages=1), f"doc{index}.pdf", PDF_MIME))
        page1 = user_a.get("/api/v1/documents?page=1&page_size=2").json()
        page2 = user_a.get("/api/v1/documents?page=2&page_size=2").json()
        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 1
        assert page1["total"] == 3

    def test_get_single_document(self, user_a: AuthedClient) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        response = user_a.get(f"/api/v1/documents/{document_id}")
        assert response.status_code == 200
        assert response.json()["id"] == document_id

    def test_unknown_document_not_found(self, user_a: AuthedClient) -> None:
        assert user_a.get(f"/api/v1/documents/{uuid.uuid4()}").status_code == 404

    def test_usage_reports_quota_state(self, user_a: AuthedClient) -> None:
        upload_pdf(user_a, "french", pages=1)
        body = user_a.get("/api/v1/documents/usage").json()
        assert body["documents"] == 1
        assert body["total_bytes"] > 0
        assert body["max_documents"] == 5


class TestDownload:
    def test_download_returns_original_bytes(self, user_a: AuthedClient) -> None:
        content = make_pdf("french", pages=1)
        document_id = user_a.upload(**upload_kwargs(content, "dl.pdf", PDF_MIME)).json()["id"]
        response = user_a.get(f"/api/v1/documents/{document_id}/download")
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_download_disposition_is_attachment(self, user_a: AuthedClient) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("arabic", pages=1), "بحث.pdf", PDF_MIME)
        ).json()["id"]
        response = user_a.get(f"/api/v1/documents/{document_id}/download")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert "filename*=UTF-8''" in disposition

    def test_download_requires_authentication(self, client, user_a: AuthedClient) -> None:  # type: ignore[no-untyped-def]
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 401


class TestDelete:
    def test_delete_removes_document_and_blob(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        run_worker(app)
        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code == 200
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert user_a.get("/api/v1/documents").json()["total"] == 0

        # The stored blob must be gone from disk too.
        from sqlalchemy import select

        from mafhum_api.db.models import Document, DocumentTextSegment

        with app.state.context.database.session() as session:
            assert (
                session.scalar(select(Document).where(Document.id == uuid.UUID(document_id)))
                is None
            )
            count = session.scalar(
                select(DocumentTextSegment.id).where(
                    DocumentTextSegment.document_id == uuid.UUID(document_id)
                )
            )
            assert count is None

    def test_delete_requires_authentication(self, client, user_a: AuthedClient) -> None:  # type: ignore[no-untyped-def]
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 401

    def test_delete_is_idempotent_404_afterwards(self, user_a: AuthedClient) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code == 200
        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code == 404

    def test_usage_decrements_after_delete(self, user_a: AuthedClient) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert user_a.get("/api/v1/documents/usage").json()["documents"] == 1
        user_a.delete(f"/api/v1/documents/{document_id}")
        assert user_a.get("/api/v1/documents/usage").json()["documents"] == 0


class TestRetry:
    def test_retry_ready_document_conflicts(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        run_worker(app)
        response = user_a.post(f"/api/v1/documents/{document_id}/retry")
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"

    def test_retry_requires_authentication(self, client, user_a: AuthedClient) -> None:  # type: ignore[no-untyped-def]
        document_id = upload_pdf(user_a, "french", pages=1).json()["id"]
        assert client.post(f"/api/v1/documents/{document_id}/retry").status_code == 401

    def test_retry_unknown_document_not_found(self, user_a: AuthedClient) -> None:
        assert user_a.post(f"/api/v1/documents/{uuid.uuid4()}/retry").status_code == 404


@pytest.mark.parametrize("kind", ["arabic", "french"])
def test_end_to_end_slice(user_a: AuthedClient, app: FastAPI, kind: str) -> None:
    """Upload -> process -> read text -> download -> delete, over real HTTP."""
    if kind == "arabic":
        response = upload_pdf(user_a, "arabic", pages=2)
    else:
        response = upload_docx(user_a, "french")
    assert response.status_code == 201
    document_id = response.json()["id"]

    run_worker(app)
    body = user_a.get(f"/api/v1/documents/{document_id}").json()
    assert body["status"] == "ready"

    text_body = user_a.get(f"/api/v1/documents/{document_id}/text").json()
    assert text_body["total"] > 0
    assert all(item["char_count"] > 0 for item in text_body["items"])

    download = user_a.get(f"/api/v1/documents/{document_id}/download")
    assert download.status_code == 200
    assert len(download.content) == body["size_bytes"]

    assert user_a.delete(f"/api/v1/documents/{document_id}").status_code == 200
    assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 404
