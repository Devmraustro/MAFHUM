"""Cross-user isolation (IDOR) matrix.

Every test asserts two things: the attacker is refused, and the response is
indistinguishable from "does not exist" so the API cannot be used to discover
another user's documents.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import ARABIC_PARAGRAPHS, make_docx, make_pdf

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

NOT_FOUND = "resource_not_found"


def make_document(user: AuthedClient, app: FastAPI, *, ready: bool = True) -> dict[str, Any]:
    response = user.upload(**upload_kwargs(make_pdf("arabic", pages=2), "خاص.pdf", PDF_MIME))
    assert response.status_code == 201, response.text
    if ready:
        run_worker(app)
        return user.get(f"/api/v1/documents/{response.json()['id']}").json()
    return response.json()


def assert_indistinguishable(response: Any) -> None:
    """The response must look exactly like a missing resource."""
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == NOT_FOUND
    # No hint that the resource exists but belongs to someone else.
    assert "permission" not in body["message"].lower()
    assert "forbidden" not in body["code"]
    assert body.get("details") in (None, {})


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "method,url_suffix",
        [
            ("get", ""),
            ("post", ""),
            ("get", "/{id}"),
            ("get", "/{id}/text"),
            ("get", "/{id}/download"),
            ("delete", "/{id}"),
            ("post", "/{id}/retry"),
        ],
    )
    def test_all_document_endpoints_reject_anonymous(
        self, client: Any, user_a: AuthedClient, app: FastAPI, method: str, url_suffix: str
    ) -> None:
        document_id = make_document(user_a, app)["id"]
        url = "/api/v1/documents" + url_suffix.replace("{id}", document_id)
        response = getattr(client, method)(url, headers={"Origin": "http://testserver"})
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    def test_sources_endpoints_reject_anonymous(self, client: Any) -> None:
        assert client.get("/api/v1/sources").status_code == 401
        assert (
            client.post(
                "/api/v1/sources",
                json={"title": "x"},
                headers={"Origin": "http://testserver"},
            ).status_code
            == 401
        )


class TestCrossUserRead:
    def test_cannot_list_another_users_documents(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        make_document(user_a, app)
        assert user_b.get("/api/v1/documents").json()["total"] == 0

    def test_cannot_read_another_users_document_metadata(self, two_users, app: FastAPI) -> None:
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        assert_indistinguishable(user_b.get(f"/api/v1/documents/{document_id}"))

    def test_cannot_read_another_users_extracted_text(self, two_users, app: FastAPI) -> None:
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        response = user_b.get(f"/api/v1/documents/{document_id}/text")
        assert_indistinguishable(response)
        # No document content leaks in the error body.
        assert ARABIC_PARAGRAPHS[0] not in response.text

    def test_cannot_download_another_users_file(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        assert_indistinguishable(user_b.get(f"/api/v1/documents/{document_id}/download"))

    def test_cannot_retry_another_users_job(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        assert_indistinguishable(user_b.post(f"/api/v1/documents/{document_id}/retry"))

    def test_cannot_delete_another_users_document(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        assert_indistinguishable(user_b.delete(f"/api/v1/documents/{document_id}"))
        # The owner's document is untouched.
        assert user_a.get(f"/api/v1/documents/{document_id}").status_code == 200

    def test_guessed_ids_do_not_bypass_ownership(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = uuid.UUID(make_document(user_a, app)["id"])
        # Try a neighbourhood of the real identifier.
        for delta in (-1, 1, 2, 1000):
            guessed = uuid.UUID(int=document_id.int + delta)
            assert user_b.get(f"/api/v1/documents/{guessed}").status_code == 404

    def test_error_does_not_reveal_existence(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        real_id = make_document(user_a, app)["id"]
        random_id = str(uuid.uuid4())
        real = user_b.get(f"/api/v1/documents/{real_id}")
        random = user_b.get(f"/api/v1/documents/{random_id}")
        assert real.status_code == random.status_code == 404
        assert real.json()["code"] == random.json()["code"]
        assert real.json()["message"] == random.json()["message"]


class TestCrossUserProject:
    def test_cannot_see_foreign_projects(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        user_a.post("/api/v1/projects", json={"title": "مشروع أ"})
        titles = [item["title"] for item in user_b.get("/api/v1/projects").json()["items"]]
        assert titles == []

    def test_cannot_attach_document_to_foreign_project(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        foreign = user_b.post("/api/v1/projects", json={"title": "مشروع ب"}).json()["id"]
        response = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "x.pdf", PDF_MIME, foreign)
        )
        assert response.status_code == 422
        # The project id is never confirmed as existing.
        assert response.json()["field_errors"]["project_id"] == ["غير متاح."]
        assert user_a.get("/api/v1/documents").json()["total"] == 0

    def test_foreign_project_filter_returns_nothing(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        project_id = user_a.post("/api/v1/projects", json={"title": "مشروع أ"}).json()["id"]
        user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "x.pdf", PDF_MIME, project_id))
        response = user_b.get(f"/api/v1/documents?project_id={project_id}")
        assert response.status_code == 200
        assert response.json()["total"] == 0


class TestCrossUserSources:
    def test_cannot_read_foreign_source(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        source_id = user_a.post("/api/v1/sources", json={"title": "مصدر خاص"}).json()["id"]
        response = user_b.get(f"/api/v1/sources/{source_id}")
        assert response.status_code == 404
        assert response.json()["code"] == NOT_FOUND

    def test_cannot_delete_foreign_source(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        source_id = user_a.post("/api/v1/sources", json={"title": "مصدر خاص"}).json()["id"]
        assert user_b.delete(f"/api/v1/sources/{source_id}").status_code == 404
        assert user_a.get(f"/api/v1/sources/{source_id}").status_code == 200

    def test_cannot_link_source_to_foreign_document(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = make_document(user_a, app)["id"]
        response = user_b.post(
            "/api/v1/sources", json={"title": "مصدر", "document_id": document_id}
        )
        assert response.status_code == 422
        assert response.json()["field_errors"]["document_id"] == ["غير متاح."]

    def test_source_list_is_scoped(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        user_a.post("/api/v1/sources", json={"title": "مصدر أ"})
        user_b.post("/api/v1/sources", json={"title": "مصدر ب"})
        assert user_a.get("/api/v1/sources").json()["total"] == 1
        assert user_b.get("/api/v1/sources").json()["total"] == 1


class TestDocxIsolation:
    def test_docx_also_isolated(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        response = user_a.upload(**upload_kwargs(make_docx("arabic"), "خاص.docx", DOCX_MIME))
        run_worker(app)
        document_id = response.json()["id"]
        assert_indistinguishable(user_b.get(f"/api/v1/documents/{document_id}"))
        assert_indistinguishable(user_b.get(f"/api/v1/documents/{document_id}/text"))
