"""Phase 6 — editable, evidence-grounded presentations.

These tests exercise the real request path: a real database, the real generator,
a real private blob and a real PPTX download. The file is then inspected as an
OOXML package, because a 201 response says nothing about whether the deck can
be edited or whether Arabic text has a direction at all.
"""

from __future__ import annotations

import uuid
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI

from mafhum_api.db.models import Document, DocumentStatus, DocumentTextSegment
from mafhum_api.services.presentations.generator import DeckGenerator
from mafhum_api.services.presentations.spec import DeckCitation, DeckSlide, DeckSpec, SpecError
from mafhum_api.services.presentations.validation import inspect
from tests.conftest import AuthedClient

ARABIC_TEXT = "تحدد مدة ولاية رئيس الجمهورية بخمس سنوات."
ARABIC_QUESTION = "ما هي مدة ولاية رئيس الجمهورية؟"
FRENCH_TEXT = "La durée du mandat présidentiel est de cinq ans."
FRENCH_QUESTION = "Quelle est la durée du mandat présidentiel ?"


def seed_document(app: FastAPI, owner_id: uuid.UUID, *, text: str, filename: str) -> str:
    """Insert an already-processed document with one citable passage."""
    with app.state.context.database.session() as db:
        document = Document(
            owner_id=owner_id,
            storage_key="ef/" + "a" * 64,
            display_filename=filename,
            format="pdf",
            size_bytes=10,
            content_sha256="0" * 64,
            status=DocumentStatus.READY.value,
            char_count=len(text),
            segment_count=1,
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentTextSegment(
                document_id=document.id,
                generation=1,
                ordinal=0,
                page_number=1,
                text=text,
                char_count=len(text),
            )
        )
        db.commit()
        return str(document.id)


def blob_count(app: FastAPI) -> int:
    root = app.state.context.storage.root
    return sum(1 for path in root.rglob("*") if path.is_file())


def create_deck(user: AuthedClient, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "title": "عرض تجريبي",
        "language": "ar",
        "question": ARABIC_QUESTION,
    }
    payload.update(overrides)
    response = user.post("/api/v1/presentations/jobs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def download_deck(user: AuthedClient, job: dict, tmp_path: Path) -> Path:
    artifact_id = job["artifact"]["id"]
    response = user.get(f"/api/v1/artifacts/{artifact_id}/download")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert "attachment" in response.headers["content-disposition"]
    target = tmp_path / "deck.pptx"
    target.write_bytes(response.content)
    return target


class TestArabicRTLAndEditability:
    def test_deck_is_editable_and_direction_is_written(
        self, user_a: AuthedClient, app: FastAPI, tmp_path: Path
    ) -> None:
        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        job = create_deck(user_a)

        assert job["status"] == "succeeded"
        assert job["language"] == "ar"
        assert job["citation_count"] == 1
        assert job["slide_count"] == 5  # title, plan, one quote, chart, table
        assert job["sources_available"] is True
        assert job["artifact"]["size_bytes"] > 10_000

        path = download_deck(user_a, job, tmp_path)
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            assert "[Content_Types].xml" in archive.namelist()
            slide_xml = archive.read("ppt/slides/slide3.xml").decode("utf-8")
            assert ARABIC_TEXT in slide_xml, "verbatim quotation must survive"
            assert 'rtl="1"' in slide_xml
            assert 'lang="ar-DZ"' in slide_xml
            assert "<a:cs " in slide_xml or "<a:cs/" in slide_xml

        report = inspect(path)
        assert report.ok, report.issues
        assert report.slides == job["slide_count"]
        assert report.chart_parts >= 1
        assert report.table_parts >= 1
        assert report.rtl_paragraphs > 0
        assert report.table_rtl_parts >= 1
        assert report.editable is True

    def test_french_deck_is_ltr_but_still_editable(
        self, user_a: AuthedClient, app: FastAPI, tmp_path: Path
    ) -> None:
        seed_document(app, user_a.user_id, text=FRENCH_TEXT, filename="fr.pdf")  # type: ignore[arg-type]
        job = create_deck(
            user_a, title="Exposé de recherche", language="fr", question=FRENCH_QUESTION
        )
        path = download_deck(user_a, job, tmp_path)
        report = inspect(path)
        assert report.ok, report.issues
        assert report.rtl_paragraphs == 0
        with zipfile.ZipFile(path) as archive:
            assert 'lang="fr-FR"' in archive.read("ppt/slides/slide1.xml").decode("utf-8")


class TestGrounding:
    def test_no_evidence_is_a_conflict_not_a_plausible_deck(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        response = user_a.post(
            "/api/v1/presentations/jobs",
            json={"title": "عرض", "language": "ar", "question": ARABIC_QUESTION},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "insufficient_evidence"
        assert user_a.get("/api/v1/presentations/jobs").json()["total"] == 0

    def test_saved_research_can_seed_a_deck(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        topic = user_a.post(
            "/api/v1/study/topics", json={"title": "موضوع", "language": "ar"}
        ).json()["id"]
        saved = user_a.post(
            f"/api/v1/study/topics/{topic}/research",
            json={
                "question": ARABIC_QUESTION,
                "answer": None,
                "status": "grounded",
                "evidence_sufficient": True,
                "coverage": 0.8,
                "citations": [
                    {
                        "kind": "document",
                        "quote": ARABIC_TEXT,
                        "document_id": document_id,
                        "title": "ar.pdf",
                        "page_number": 1,
                        "ordinal": 0,
                        "locator": "ar.pdf — p. 1 — §0",
                        "score": 0.8,
                    }
                ],
                "source_document_ids": [document_id],
            },
        )
        assert saved.status_code == 201, saved.text
        job = create_deck(user_a, question=None, saved_research_id=saved.json()["id"])
        assert job["status"] == "succeeded"
        assert job["citation_count"] == 1

    def test_deleted_source_flips_availability_on_read(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        job = create_deck(user_a)
        assert job["sources_available"] is True

        removed = user_a.delete(f"/api/v1/documents/{document_id}")
        assert removed.status_code == 200, removed.text
        reread = user_a.get(f"/api/v1/presentations/jobs/{job['id']}").json()
        assert reread["sources_available"] is False
        assert reread["citations"][0]["available"] is False


class TestIsolationAndLifecycle:
    def test_other_user_gets_404_everywhere(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI, tmp_path: Path
    ) -> None:
        user_a, user_b = two_users
        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        job = create_deck(user_a)
        job_id = job["id"]
        artifact_id = job["artifact"]["id"]

        assert user_b.get(f"/api/v1/presentations/jobs/{job_id}").status_code == 404
        assert user_b.get(f"/api/v1/artifacts/{artifact_id}/download").status_code == 404
        assert user_b.delete(f"/api/v1/presentations/jobs/{job_id}").status_code == 404
        assert user_b.get("/api/v1/presentations/jobs").json()["total"] == 0
        assert user_a.get(f"/api/v1/artifacts/{artifact_id}/download").status_code == 200

    def test_deleting_a_job_removes_its_blob(self, user_a: AuthedClient, app: FastAPI) -> None:
        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        job = create_deck(user_a)
        assert blob_count(app) == 1
        response = user_a.delete(f"/api/v1/presentations/jobs/{job['id']}")
        assert response.status_code == 200
        assert blob_count(app) == 0
        assert user_a.get(f"/api/v1/presentations/jobs/{job['id']}").status_code == 404

    def test_account_deletion_erases_presentation_bytes(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        job = create_deck(user_a)
        assert blob_count(app) == 1
        with app.state.context.database.session() as db:
            report = app.state.context.deletion.delete_account(db, user_a.user_id)  # type: ignore[arg-type]
        assert report.complete is True, report
        assert blob_count(app) == 0
        assert user_a.get(f"/api/v1/presentations/jobs/{job['id']}").status_code in (401, 404)

    def test_quota_is_enforced(self, user_a: AuthedClient, app: FastAPI) -> None:
        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        service = app.state.context.presentations
        service._limits = replace(service._limits, max_jobs_per_user=1)
        create_deck(user_a)
        response = user_a.post(
            "/api/v1/presentations/jobs",
            json={"title": "عرض ثان", "language": "ar", "question": ARABIC_QUESTION},
        )
        assert response.status_code == 429
        assert response.json()["code"] == "quota_exceeded"

    def test_generator_failure_is_recorded_not_hidden(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        class Broken:
            name = "broken"
            version = "0"

            def generate(self, spec: DeckSpec, output: Path) -> None:
                raise RuntimeError("private-filesystem-path-should-not-leak")

        seed_document(app, user_a.user_id, text=ARABIC_TEXT, filename="ar.pdf")  # type: ignore[arg-type]
        service = app.state.context.presentations
        original: DeckGenerator = service._generator
        service._generator = Broken()  # type: ignore[assignment]
        try:
            response = user_a.post(
                "/api/v1/presentations/jobs",
                json={"title": "عرض", "language": "ar", "question": ARABIC_QUESTION},
            )
        finally:
            service._generator = original

        assert response.status_code == 503
        assert "private-filesystem-path" not in response.text
        jobs = user_a.get("/api/v1/presentations/jobs").json()
        assert jobs["total"] == 1
        assert jobs["items"][0]["status"] == "failed"
        assert jobs["items"][0]["error_code"] == "generation_failed"


class TestSpecBounds:
    def test_rejects_more_slides_than_allowed(self) -> None:
        slides = tuple(DeckSlide(kind="bullets", title=f"slide {i}") for i in range(13))
        spec = DeckSpec(
            title="x",
            subtitle="",
            language="ar",
            rtl=True,
            draft_notice="d",
            slides=slides,
            citations=(),
        )
        with pytest.raises(SpecError):
            spec.validate()

    def test_rejects_unresolvable_citation_reference(self) -> None:
        spec = DeckSpec(
            title="x",
            subtitle="",
            language="fr",
            rtl=False,
            draft_notice="d",
            slides=(DeckSlide(kind="quote", title="q", text="t", citation_indices=(2,)),),
            citations=(DeckCitation(index=1, quote="q", locator="l"),),
        )
        with pytest.raises(SpecError):
            spec.validate()
