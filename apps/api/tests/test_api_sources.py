"""Academic source catalog tests, focused on the anti-fabrication rules."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"


class TestCreate:
    def test_minimal_source(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "مدخل إلى الخوارزميات"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["title"] == "مدخل إلى الخوارزميات"
        assert body["provenance"] == "user_supplied"
        assert body["verification_status"] == "user_asserted"

    def test_unknown_fields_stay_null(self, user_a: AuthedClient) -> None:
        """Nothing is inferred: absent metadata must remain unknown."""
        body = user_a.post("/api/v1/sources", json={"title": "عنوان فقط"}).json()
        for field in (
            "authors",
            "institution",
            "publisher",
            "publication_year",
            "doi",
            "isbn",
            "url",
            "repository_identifier",
            "language",
            "page_count",
        ):
            assert body[field] is None, field

    def test_never_marked_verified_by_user_input(self, user_a: AuthedClient) -> None:
        body = user_a.post(
            "/api/v1/sources",
            json={
                "title": "بحث",
                "authors": ["مؤلف"],
                "doi": "10.1234/abcd",
                "publication_year": 2024,
                "institution": "جامعة سطيف",
            },
        ).json()
        assert body["verification_status"] == "user_asserted"
        assert body["verified_by"] is None
        assert body["verified_at"] is None

    def test_full_metadata_round_trip(self, user_a: AuthedClient) -> None:
        payload = {
            "title": "Analyse des algorithmes",
            "authors": ["Amina Test", "Bilal Example"],
            "institution": "Université de Sétif",
            "publisher": "Éditions Synthétiques",
            "publication_year": 2021,
            "source_type": "book",
            "isbn": "978-2-1234-5678-9",
            "url": "https://example.org/book",
            "language": "fr",
            "page_count": 320,
            "notes": "Fictional record used for tests.",
        }
        body = user_a.post("/api/v1/sources", json=payload).json()
        for key, value in payload.items():
            assert body[key] == value

    def test_empty_authors_list_becomes_null(self, user_a: AuthedClient) -> None:
        body = user_a.post("/api/v1/sources", json={"title": "عنوان", "authors": []}).json()
        assert body["authors"] is None

    def test_requires_title_or_url_or_document(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/sources", json={"institution": "جامعة"})
        assert response.status_code == 422
        assert response.json()["field_errors"]["title"]

    def test_unauthenticated_rejected(self, client: Any) -> None:
        assert (
            client.post(
                "/api/v1/sources",
                json={"title": "x"},
                headers={"Origin": "http://testserver"},
            ).status_code
            == 401
        )


class TestValidation:
    @pytest.mark.parametrize(
        "doi",
        ["not-a-doi", "10.1234", "doi:10.1234/abc", "http://doi.org/10.1234/abc", "11/abc"],
    )
    def test_invalid_doi_rejected(self, user_a: AuthedClient, doi: str) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "عنوان", "doi": doi})
        assert response.status_code == 422
        assert "doi" in response.json()["field_errors"]

    @pytest.mark.parametrize("doi", ["10.1234/abcd", "10.99999/x.y.z", "10.1000/182"])
    def test_valid_doi_accepted(self, user_a: AuthedClient, doi: str) -> None:
        assert user_a.post("/api/v1/sources", json={"title": "t", "doi": doi}).status_code == 201

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.org/x",
            "javascript:alert(1)",
            "example.org",
            "//example.org",
            "file:///etc/passwd",
        ],
    )
    def test_non_http_url_rejected(self, user_a: AuthedClient, url: str) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "عنوان", "url": url})
        assert response.status_code == 422
        assert "url" in response.json()["field_errors"]

    @pytest.mark.parametrize("url", ["https://example.org/a", "http://example.org"])
    def test_http_url_accepted(self, user_a: AuthedClient, url: str) -> None:
        assert user_a.post("/api/v1/sources", json={"title": "t", "url": url}).status_code == 201

    @pytest.mark.parametrize("year", [999, 2101, 0, -5])
    def test_implausible_year_rejected(self, user_a: AuthedClient, year: int) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "عنوان", "publication_year": year})
        assert response.status_code == 422

    def test_unknown_source_type_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "t", "source_type": "magic"})
        assert response.status_code == 422

    def test_invalid_language_code_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "t", "language": "not-a-lang!!"})
        assert response.status_code == 422

    def test_zero_page_count_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/sources", json={"title": "t", "page_count": 0})
        assert response.status_code == 422

    def test_too_many_authors_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post(
            "/api/v1/sources", json={"title": "t", "authors": [f"a{i}" for i in range(80)]}
        )
        assert response.status_code == 422


class TestVerificationIntegrity:
    def test_verified_requires_verifier(self, app: FastAPI) -> None:
        """The database refuses a 'verified' record with no verifier recorded."""
        from mafhum_api.db.models import SourceRecord
        from mafhum_api.services.identity.service import IdentityService

        context = app.state.context
        with context.database.session() as session:
            user = IdentityService(session_ttl_minutes=60).register(
                session,
                email="verif@example.com",
                password="long-enough-password",
                display_name="v",
            )
            session.commit()
            owner_id = user.id

        with context.database.session() as session:
            session.add(
                SourceRecord(
                    id=uuid.uuid4(),
                    owner_id=owner_id,
                    title="مصدر",
                    verification_status="verified",
                    verified_by=None,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

    def test_editing_downgrades_verified_to_user_asserted(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """An owner edit is an assertion, never a verification."""
        from mafhum_api.db.models import SourceRecord

        source_id = uuid.UUID(user_a.post("/api/v1/sources", json={"title": "مصدر"}).json()["id"])
        # Simulate a verified record (only an admin process can set this).
        with app.state.context.database.session() as session:
            record = session.get(SourceRecord, source_id)
            assert record is not None
            record.verification_status = "verified"
            record.verified_by = "catalog-review"
            session.commit()
        assert (
            user_a.get(f"/api/v1/sources/{source_id}").json()["verification_status"] == "verified"
        )

        user_a.patch(f"/api/v1/sources/{source_id}", json={"title": "مصدر معدّل"})
        body = user_a.get(f"/api/v1/sources/{source_id}").json()
        assert body["verification_status"] == "user_asserted"
        assert body["verified_by"] is None
        assert body["title"] == "مصدر معدّل"


class TestDocumentLinkage:
    def test_link_to_own_document(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("arabic", pages=1), "a.pdf", PDF_MIME)
        ).json()["id"]
        run_worker(app)
        body = user_a.post(
            "/api/v1/sources", json={"title": "مصدر", "document_id": document_id}
        ).json()
        assert body["document_id"] == document_id

    def test_document_metadata_extraction_leaves_unknowns_null(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """Extraction provenance must not invent authors or a year."""
        from mafhum_api.db.models import Document

        document_id = uuid.UUID(
            user_a.upload(**upload_kwargs(make_pdf("arabic", pages=2), "a.pdf", PDF_MIME)).json()[
                "id"
            ]
        )
        run_worker(app)
        with app.state.context.database.session() as session:
            document = session.get(Document, document_id)
            assert document is not None
            record = app.state.context.sources.create_from_document_metadata(
                session,
                owner_id=document.owner_id,
                document=document,
                title=None,
                authors=None,
                page_count=document.page_count,
                language=document.language_hint,
                parser_name=document.parser_name,
                parser_version=document.parser_version,
            )
            session.commit()
            source_id = record.id

        body = user_a.get(f"/api/v1/sources/{source_id}").json()
        assert body["provenance"] == "document_extracted"
        assert body["verification_status"] == "unverified"
        assert body["title"] is None
        assert body["authors"] is None
        assert body["publication_year"] is None
        assert body["doi"] is None
        assert body["url"] is None
        # Only what the parser actually produced is recorded.
        assert body["page_count"] == 2
        assert body["language"] == "ar"
        assert body["parser_name"] == "pypdf"

    def test_link_to_foreign_document_rejected(self, two_users, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME)
        ).json()["id"]
        response = user_b.post("/api/v1/sources", json={"title": "x", "document_id": document_id})
        assert response.status_code == 422

    def test_link_to_unknown_document_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post(
            "/api/v1/sources", json={"title": "x", "document_id": str(uuid.uuid4())}
        )
        assert response.status_code == 422


class TestCrud:
    def test_list_and_pagination(self, user_a: AuthedClient) -> None:
        for index in range(3):
            user_a.post("/api/v1/sources", json={"title": f"مصدر {index}"})
        body = user_a.get("/api/v1/sources?page=1&page_size=2").json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["page_size"] == 2

    def test_update(self, user_a: AuthedClient) -> None:
        source_id = user_a.post("/api/v1/sources", json={"title": "قبل"}).json()["id"]
        body = user_a.patch(
            f"/api/v1/sources/{source_id}", json={"title": "بعد", "language": "ar"}
        ).json()
        assert body["title"] == "بعد"
        assert body["language"] == "ar"

    def test_delete(self, user_a: AuthedClient) -> None:
        source_id = user_a.post("/api/v1/sources", json={"title": "للحذف"}).json()["id"]
        assert user_a.delete(f"/api/v1/sources/{source_id}").status_code == 204
        assert user_a.get(f"/api/v1/sources/{source_id}").status_code == 404

    def test_get_unknown(self, user_a: AuthedClient) -> None:
        assert user_a.get(f"/api/v1/sources/{uuid.uuid4()}").status_code == 404

    def test_no_network_fetch_is_performed(self, user_a: AuthedClient) -> None:
        """A URL is stored as data; this phase never resolves or fetches it."""
        body = user_a.post(
            "/api/v1/sources",
            json={"title": "مصدر", "url": "https://nonexistent.invalid/x"},
        ).json()
        assert body["url"] == "https://nonexistent.invalid/x"
        assert body["verification_status"] == "user_asserted"
