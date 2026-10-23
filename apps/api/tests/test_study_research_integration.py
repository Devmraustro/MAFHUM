"""Phase 5 <-> Phase 4 integration: saving research into a study workspace.

The workflow under test: a student runs a research query, saves a result into a
topic (existing or newly created), reopens it later, and can still tell whether
the sources behind it exist.

Two properties matter more than the happy path:

* **Isolation is in the backend.** Every cross-user test asserts 404, because the
  service filters on owner_id in the query. A 403 would confirm the row exists.
* **A deleted source must not become a lie.** The saved snapshot survives -- that
  is the point of saving -- but ``sources_available`` flips to false and the
  missing ids are named. A stale ``true`` would leave a citation that looks
  checkable when it is not.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.conftest import AuthedClient
from tests.test_api_lifecycle import make_ready_document

PDF_MIME = "application/pdf"


def _topic(user: AuthedClient, title: str = "موضوع") -> str:
    response = user.post("/api/v1/study/topics", json={"title": title, "language": "ar"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _research_payload(document_id: str, question: str = "ما هي مدة الولاية؟") -> dict:
    return {
        "question": question,
        "answer": "الإجابة مبنية حصريًا على المقاطع المقتبسة.",
        "status": "grounded",
        "evidence_sufficient": True,
        "coverage": 0.67,
        "citations": [
            {
                "kind": "document",
                "quote": "تحدد مدة ولاية رئيس الجمهورية بخمس سنوات.",
                "document_id": document_id,
                "title": "doc.pdf",
                "page_number": 12,
                "ordinal": 3,
                "locator": "doc.pdf — p. 12 — §3",
                "score": 0.67,
            }
        ],
        "source_document_ids": [document_id],
    }


class TestSaveAndRetrieve:
    def test_saved_result_is_returned_with_provenance(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)

        saved = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        )
        assert saved.status_code == 201, saved.text
        body = saved.json()
        assert body["question"] == "ما هي مدة الولاية؟"
        assert body["status"] == "grounded"
        assert body["evidence_sufficient"] is True
        assert body["source_document_ids"] == [document_id]
        assert body["sources_available"] is True
        assert body["citations"][0]["quote"].startswith("تحدد مدة")
        assert body["retrieved_at"] is not None

    def test_saved_material_survives_a_restart(self, user_a: AuthedClient, app: FastAPI) -> None:
        """Persistence is in the database, not in process memory."""
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        saved_id = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        ).json()["id"]

        # Read straight from the database, bypassing the API and the service.
        with app.state.context.database.session() as db:
            from mafhum_api.db.models import SavedResearch

            row = db.get(SavedResearch, uuid.UUID(saved_id))
            assert row is not None
            assert row.question == "ما هي مدة الولاية؟"
            assert list(row.source_document_ids) == [document_id]
            assert row.status == "grounded"

    def test_saved_results_are_listed_for_the_topic(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json=_research_payload(document_id, "السؤال الأول"),
        )
        user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json=_research_payload(document_id, "السؤال الثاني"),
        )

        listing = user_a.get(f"/api/v1/study/topics/{topic_id}/research")
        assert listing.status_code == 200
        questions = {item["question"] for item in listing.json()["items"]}
        assert questions == {"السؤال الأول", "السؤال الثاني"}

    def test_a_saved_result_can_be_reopened_by_id(self, user_a: AuthedClient, app: FastAPI) -> None:
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        saved_id = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        ).json()["id"]

        reopened = user_a.get(f"/api/v1/study/research/{saved_id}")
        assert reopened.status_code == 200
        assert reopened.json()["id"] == saved_id
        assert reopened.json()["citations"][0]["page_number"] == 12

    def test_saving_into_a_newly_created_topic_works(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """Create-then-save is the supported path for a new topic."""
        document_id = make_ready_document(user_a, app)
        created = user_a.post(
            "/api/v1/study/topics", json={"title": "موضوع جديد", "language": "fr"}
        )
        assert created.status_code == 201
        topic_id = created.json()["id"]

        saved = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json=_research_payload(document_id, "Quelle est la durée ?"),
        )
        assert saved.status_code == 201
        assert saved.json()["topic_id"] == topic_id


class TestDuplicateSaves:
    def test_duplicate_save_returns_the_existing_row(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """A retried request must not multiply the student's saved material."""
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        payload = _research_payload(document_id)

        first = user_a.post(f"/api/v1/study/topics/{topic_id}/research", json=payload)
        second = user_a.post(f"/api/v1/study/topics/{topic_id}/research", json=payload)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        listing = user_a.get(f"/api/v1/study/topics/{topic_id}/research").json()["items"]
        assert len(listing) == 1

    def test_the_same_question_in_two_topics_stays_separate(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """Deduplication is scoped to the topic, not global."""
        document_id = make_ready_document(user_a, app)
        first = _topic(user_a, "الأول")
        second = _topic(user_a, "الثاني")
        payload = _research_payload(document_id)

        a = user_a.post(f"/api/v1/study/topics/{first}/research", json=payload).json()
        b = user_a.post(f"/api/v1/study/topics/{second}/research", json=payload).json()
        assert a["id"] != b["id"]


class TestInsufficientResultsAreNotLaundered:
    def test_insufficient_evidence_is_preserved_on_save(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """A research failure must not turn into confident study material."""
        topic_id = _topic(user_a)
        payload = {
            "question": "سؤال بلا دليل",
            "answer": None,
            "status": "insufficient_evidence",
            "evidence_sufficient": False,
            "coverage": 0.05,
            "citations": [],
            "source_document_ids": [],
        }
        saved = user_a.post(f"/api/v1/study/topics/{topic_id}/research", json=payload)
        assert saved.status_code == 201
        body = saved.json()
        assert body["status"] == "insufficient_evidence"
        assert body["evidence_sufficient"] is False
        assert body["answer"] is None
        assert body["citations"] == []

        # And it still says so when reopened later.
        reopened = user_a.get(f"/api/v1/study/research/{body['id']}").json()
        assert reopened["status"] == "insufficient_evidence"
        assert reopened["evidence_sufficient"] is False

    def test_invalid_status_is_rejected(self, user_a: AuthedClient) -> None:
        topic_id = _topic(user_a)
        response = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={
                "question": "سؤال",
                "status": "definitely_correct",
                "citations": [],
                "source_document_ids": [],
            },
        )
        assert response.status_code == 422


class TestSourceLossIsVisible:
    def test_deleting_the_document_marks_the_saved_result_unavailable(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """The snapshot survives; the claim that the source exists does not."""
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        saved = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        ).json()
        assert saved["sources_available"] is True

        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code in (200, 204)

        reopened = user_a.get(f"/api/v1/study/research/{saved['id']}").json()
        assert reopened["sources_available"] is False
        assert reopened["missing_document_ids"] == [document_id]
        assert reopened["present_document_ids"] == []
        # The saved text is still there -- losing the source must not delete the
        # student's material, it must stop vouching for it.
        assert reopened["citations"][0]["quote"].startswith("تحدد مدة")

    def test_the_listing_also_reports_unavailability(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        )
        assert user_a.delete(f"/api/v1/documents/{document_id}").status_code in (200, 204)

        items = user_a.get(f"/api/v1/study/topics/{topic_id}/research").json()["items"]
        assert len(items) == 1
        assert items[0]["sources_available"] is False

    def test_an_unparseable_document_id_is_treated_as_missing(self, user_a: AuthedClient) -> None:
        """Garbage in provenance must not be reported as available."""
        topic_id = _topic(user_a)
        saved = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={
                "question": "سؤال",
                "answer": "جواب",
                "status": "grounded",
                "evidence_sufficient": True,
                "coverage": 0.5,
                "citations": [],
                "source_document_ids": ["not-a-uuid"],
            },
        )
        assert saved.status_code == 201
        body = saved.json()
        assert body["sources_available"] is False
        assert body["missing_document_ids"] == ["not-a-uuid"]

    def test_an_unknown_document_id_is_treated_as_missing(self, user_a: AuthedClient) -> None:
        topic_id = _topic(user_a)
        ghost = str(uuid.uuid4())
        body = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={
                "question": "سؤال",
                "answer": "جواب",
                "status": "grounded",
                "evidence_sufficient": True,
                "coverage": 0.5,
                "citations": [],
                "source_document_ids": [ghost],
            },
        ).json()
        assert body["sources_available"] is False
        assert body["missing_document_ids"] == [ghost]


class TestCrossUserIsolation:
    def test_another_user_cannot_read_a_saved_result(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI
    ) -> None:
        user_a, user_b = two_users
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        saved_id = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
        ).json()["id"]

        # 404, not 403: the response must not confirm the row exists.
        assert user_b.get(f"/api/v1/study/research/{saved_id}").status_code == 404
        assert user_b.get(f"/api/v1/study/topics/{topic_id}/research").status_code == 404

    def test_another_user_cannot_save_into_the_topic(
        self, two_users: tuple[AuthedClient, AuthedClient], app: FastAPI
    ) -> None:
        user_a, user_b = two_users
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)

        assert (
            user_b.post(
                f"/api/v1/study/topics/{topic_id}/research", json=_research_payload(document_id)
            ).status_code
            == 404
        )

    def test_a_random_saved_id_is_not_found_for_its_own_owner(self, user_a: AuthedClient) -> None:
        assert user_a.get(f"/api/v1/study/research/{uuid.uuid4()}").status_code == 404


class TestInputLimits:
    def test_oversized_question_rejected(self, user_a: AuthedClient) -> None:
        topic_id = _topic(user_a)
        response = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={
                "question": "س" * 5000,
                "status": "grounded",
                "citations": [],
                "source_document_ids": [],
            },
        )
        assert response.status_code == 422

    def test_empty_question_rejected(self, user_a: AuthedClient) -> None:
        topic_id = _topic(user_a)
        response = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={"question": "", "status": "grounded", "citations": [], "source_document_ids": []},
        )
        assert response.status_code == 422

    def test_missing_status_rejected(self, user_a: AuthedClient) -> None:
        topic_id = _topic(user_a)
        response = user_a.post(
            f"/api/v1/study/topics/{topic_id}/research",
            json={"question": "سؤال", "citations": [], "source_document_ids": []},
        )
        assert response.status_code == 422


class TestTransactionSafety:
    def test_an_uncommitted_save_leaves_nothing_behind(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """The row only becomes durable on commit.

        A save that fails before committing must not leave a half-written record
        behind. This exercises the real session and the real service method --
        the only thing changed is that the transaction is rolled back instead of
        committed, which is what any commit failure amounts to.
        """
        topic_id = uuid.UUID(_topic(user_a))
        user_id = user_a.user_id

        context = app.state.context
        with context.database.session() as db:
            record = context.study.save_research(
                db,
                owner_id=user_id,
                topic_id=topic_id,
                question="سؤال لم يكتمل حفظه",
                answer="جواب",
                status="grounded",
                evidence_sufficient=True,
                coverage=0.5,
                citations=[],
                source_document_ids=[],
            )
            saved_id = record.id
            # Simulate the commit failing: the transaction is discarded.
            db.rollback()

        with context.database.session() as db:
            from mafhum_api.db.models import SavedResearch

            assert db.get(SavedResearch, saved_id) is None

        # And the API agrees: nothing was saved.
        assert user_a.get(f"/api/v1/study/topics/{topic_id}/research").json()["items"] == []

    def test_saving_twice_after_a_rollback_still_deduplicates(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """A rolled-back save must not poison the deduplication check."""
        document_id = make_ready_document(user_a, app)
        topic_id = _topic(user_a)
        payload = _research_payload(document_id)

        first = user_a.post(f"/api/v1/study/topics/{topic_id}/research", json=payload)
        second = user_a.post(f"/api/v1/study/topics/{topic_id}/research", json=payload)
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["deduplicated"] is True
