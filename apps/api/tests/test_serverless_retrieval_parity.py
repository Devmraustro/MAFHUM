"""Retrieval and grounding parity for the serverless path (Phase H audit).

The migration added two new paths bytes and jobs can take: chunked upload
sessions (Phase E) and the processing tick (Phase F). Grounding, citation
integrity, and user isolation were proven for the original path; these tests
prove the guarantees survive the new ones end-to-end:

* a document uploaded in chunks and processed by a *tick* (no resident
  worker) produces the same grounded, cited answers as a direct upload
  processed by the worker;
* the answer is extractive — every quote literally appears in the stored
  segments — and never cites another user's document;
* anonymous access is refused on every new endpoint;
* the tick itself leaks no document data to the user who fired it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import AuthedClient
from tests.fixtures_content import make_pdf
from tests.test_api_lifecycle import make_ready_document

PDF_MIME = "application/pdf"
SESSIONS = "/api/v1/documents/upload-sessions"
TICK = "/api/v1/processing/tick"
NOT_FOUND = "resource_not_found"


def _chunked_upload(user: AuthedClient, data: bytes, filename: str = "chunked.pdf") -> str:
    """Upload via sessions + parts, drive processing with a tick, return the
    ready document's id."""
    created = user.post(
        SESSIONS,
        json={"filename": filename, "content_type": PDF_MIME, "total_bytes": len(data)},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    part_size = body["part_size"]
    for number, i in enumerate(range(0, len(data), part_size), start=1):
        part = user.put(
            f"{SESSIONS}/{body['id']}/parts/{number}",
            content=data[i : i + part_size],
            headers={"Content-Type": "application/octet-stream"},
        )
        assert part.status_code == 200, part.text
    finalized = user.post(f"{SESSIONS}/{body['id']}/finalize")
    assert finalized.status_code == 201, finalized.text
    tick = user.post(TICK)
    assert tick.status_code == 200, tick.text
    assert tick.json()["succeeded"] == 1, tick.text
    document_id = finalized.json()["id"]
    status = user.get(f"/api/v1/documents/{document_id}").json()["status"]
    assert status == "ready", status
    return document_id


def _segments_text(user: AuthedClient, document_id: str) -> str:
    body = user.get(f"/api/v1/documents/{document_id}/text").json()
    return "".join(segment["text"] for segment in body["items"])


class TestServerlessGroundingParity:
    def test_chunked_and_ticked_document_yields_grounded_cited_answers(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        data = make_pdf("arabic", pages=2)
        chunked_id = _chunked_upload(user_a, data)
        worker_id = make_ready_document(user_a, app)

        # Identical extraction: the paths converge on the same segments.
        assert _segments_text(user_a, chunked_id) == _segments_text(user_a, worker_id)

        topic = user_a.post("/api/v1/study/topics", json={"title": "موضوع الخوارزميات"})
        assert topic.status_code == 201, topic.text
        topic_id = topic.json()["id"]
        attached = user_a.post(
            f"/api/v1/study/topics/{topic_id}/materials", json={"document_id": chunked_id}
        )
        assert attached.status_code == 201, attached.text

        answer = user_a.post(
            f"/api/v1/study/topics/{topic_id}/ask",
            json={"question": "تحليل الخوارزميات وأهميته في بناء برامج فعالة للطالب"},
        )
        assert answer.status_code == 201, answer.text
        body = answer.json()
        assert body["status"] == "grounded", body
        assert body["citations"], "a grounded answer must carry its citations"

        # Extractive integrity: every quoted passage literally appears in
        # the stored segments of the cited document, and only that document.
        stored = _segments_text(user_a, chunked_id)
        for citation in body["citations"]:
            assert citation["document_id"] == chunked_id
            assert citation["quote"].strip()
            assert citation["quote"].strip() in stored, citation["quote"]

    def test_cross_user_grounding_never_cites_the_other_document(
        self, user_a: AuthedClient, user_b: AuthedClient, app: FastAPI
    ) -> None:
        """Two users hold near-identical documents; each answer may only
        cite its own user's materials — no cross-tenant leakage through
        retrieval."""
        data = make_pdf("arabic", pages=2)
        document_a = _chunked_upload(user_a, data)
        document_b = make_ready_document(user_b, app)

        for user, document_id in ((user_a, document_a), (user_b, document_b)):
            topic = user.post("/api/v1/study/topics", json={"title": "عزل"})
            topic_id = topic.json()["id"]
            assert (
                user.post(
                    f"/api/v1/study/topics/{topic_id}/materials",
                    json={"document_id": document_id},
                ).status_code
                == 201
            )
            answer = user.post(
                f"/api/v1/study/topics/{topic_id}/ask",
                json={"question": "تحليل الخوارزميات وأهميته في بناء برامج فعالة للطالب"},
            ).json()
            assert answer["status"] == "grounded", answer
            assert answer["citations"]
            for citation in answer["citations"]:
                assert citation["document_id"] == document_id

        # And neither user can attach the other's document at all.
        topic_b = user_b.post("/api/v1/study/topics", json={"title": "سرقة"}).json()["id"]
        stolen = user_b.post(
            f"/api/v1/study/topics/{topic_b}/materials", json={"document_id": document_a}
        )
        assert stolen.status_code == 404, stolen.text
        assert stolen.json()["code"] == NOT_FOUND


class TestNewEndpointIsolation:
    @pytest.mark.parametrize(
        "method,url",
        [
            ("post", SESSIONS),
            ("put", f"{SESSIONS}/00000000-0000-0000-0000-000000000000/parts/1"),
            ("post", f"{SESSIONS}/00000000-0000-0000-0000-000000000000/finalize"),
            ("delete", f"{SESSIONS}/00000000-0000-0000-0000-000000000000"),
            ("post", TICK),
            ("post", "/api/v1/processing/tick-scheduler"),
        ],
    )
    def test_new_endpoints_reject_anonymous(self, client: Any, method: str, url: str) -> None:
        kwargs: dict[str, Any] = {"content": b"x"} if method == "put" else {}
        response = getattr(client, method)(url, **kwargs)
        # 401 for protected routes; the scheduler endpoint is disabled
        # outright (404) while no token is configured — both refuse.
        assert response.status_code in (401, 404), f"{method} {url}: {response.status_code}"

    def test_tick_response_carries_no_document_identifiers(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """A user who fires the tick processes the global queue, but the
        response must not disclose whose documents ran."""
        make_ready_document(user_a, app)
        body = user_a.post(TICK).json()
        serialized = str(body)
        assert set(body) == {
            "recovered_stale_jobs",
            "expired_upload_sessions",
            "processed",
            "succeeded",
            "failed",
            "stopped_reason",
        }
        assert "-" not in serialized  # no uuids, no filenames, no keys
