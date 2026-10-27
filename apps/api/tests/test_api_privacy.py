"""Privacy, redaction and error-hygiene tests.

These assert what must never appear: credentials, session tokens, document
content, absolute storage paths, SQL text or tracebacks.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import AuthedClient, run_worker, upload_kwargs
from tests.fixtures_content import (
    ARABIC_PARAGRAPHS,
    make_corrupt_pdf,
    make_pdf,
    make_scanned_like_pdf,
)

PDF_MIME = "application/pdf"


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@pytest.fixture
def log_capture() -> Any:
    from mafhum_api.core.logging_setup import RedactingFormatter

    handler = CapturingHandler()
    handler.setFormatter(RedactingFormatter())
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


class TestRedaction:
    def test_password_values_redacted(self) -> None:
        from mafhum_api.core.logging_setup import redact_mapping

        result = redact_mapping({"password": "hunter2", "email": "a@b.c"})
        assert result["password"] == "[REDACTED]"
        assert result["email"] == "a@b.c"

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "token",
            "session_token",
            "cookie",
            "authorization",
            "api_key",
            "secret",
            "csrf_token",
            "extracted_text",
            "document_text",
            "body",
            "payload",
        ],
    )
    def test_sensitive_keys_always_redacted(self, key: str) -> None:
        from mafhum_api.core.logging_setup import redact_mapping

        assert redact_mapping({key: "sensitive-value"})[key] == "[REDACTED]"

    def test_nested_sensitive_keys_redacted(self) -> None:
        from mafhum_api.core.logging_setup import redact_mapping

        result = redact_mapping({"user": {"password": "x", "name": "طالب"}})
        assert result["user"]["password"] == "[REDACTED]"
        assert result["user"]["name"] == "طالب"

    def test_inline_secret_patterns_scrubbed(self) -> None:
        from mafhum_api.core.logging_setup import redact_text

        assert "hunter2" not in redact_text("login password=hunter2 for user")
        assert "abc123" not in redact_text("Authorization: Bearer abc123")

    def test_absolute_paths_scrubbed(self) -> None:
        from mafhum_api.core.logging_setup import redact_text

        result = redact_text("failed writing /home/user/MAFHUM/data/mafhum-storage/ab/file")
        assert "/home/user" not in result
        assert "mafhum-storage" not in result

    def test_long_text_truncated(self) -> None:
        from mafhum_api.core.logging_setup import redact_text

        assert len(redact_text("x" * 5000)) < 300

    def test_arabic_preserved_by_redaction(self) -> None:
        from mafhum_api.core.logging_setup import redact_text

        assert redact_text("نص عربي قصير") == "نص عربي قصير"


class TestLogsDuringFlows:
    def test_upload_and_processing_leave_no_document_text(
        self, user_a: AuthedClient, app: FastAPI, log_capture: CapturingHandler
    ) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_pdf("arabic", pages=2), "درس.pdf", PDF_MIME)
        ).json()["id"]
        run_worker(app)
        user_a.get(f"/api/v1/documents/{document_id}/text")
        blob = "\n".join(log_capture.records)
        for paragraph in ARABIC_PARAGRAPHS:
            assert paragraph not in blob

    def test_password_never_logged(self, client: Any, log_capture: CapturingHandler) -> None:
        password = "uniquely-identifiable-password-value"
        client.post(
            "/api/v1/auth/register",
            json={"email": "logs@example.com", "password": password},
            headers={"Origin": "http://testserver"},
        )
        client.post(
            "/api/v1/auth/login",
            json={"email": "logs@example.com", "password": password},
            headers={"Origin": "http://testserver"},
        )
        assert password not in "\n".join(log_capture.records)

    def test_session_token_never_logged(
        self, user_a: AuthedClient, log_capture: CapturingHandler
    ) -> None:
        user_a.get("/api/v1/auth/me")
        token = user_a._client.cookies.get("mafhum_session")
        assert token
        assert token not in "\n".join(log_capture.records)

    def test_storage_paths_never_logged(
        self, user_a: AuthedClient, app: FastAPI, log_capture: CapturingHandler
    ) -> None:
        user_a.upload(**upload_kwargs(make_pdf("french", pages=1), "a.pdf", PDF_MIME))
        blob = "\n".join(log_capture.records)
        assert str(app.state.context.storage.root) not in blob

    def test_failed_processing_logs_no_parser_internals(
        self, user_a: AuthedClient, app: FastAPI, log_capture: CapturingHandler
    ) -> None:
        user_a.upload(**upload_kwargs(make_scanned_like_pdf(), "scan.pdf", PDF_MIME))
        run_worker(app)
        blob = "\n".join(log_capture.records)
        assert "Traceback" not in blob
        assert str(app.state.context.storage.root) not in blob

    def test_corrupt_upload_logs_no_path(
        self, user_a: AuthedClient, app: FastAPI, log_capture: CapturingHandler
    ) -> None:
        user_a.upload(**upload_kwargs(make_corrupt_pdf(), "bad.pdf", PDF_MIME))
        blob = "\n".join(log_capture.records)
        assert str(app.state.context.storage.root) not in blob


class TestErrorHygiene:
    def _assert_clean(self, body: dict[str, Any], app: FastAPI) -> None:
        text = json.dumps(body, ensure_ascii=False)
        assert "Traceback" not in text
        assert 'File "' not in text
        assert "sqlalchemy" not in text.lower()
        assert "SELECT " not in text
        assert str(app.state.context.storage.root) not in text
        assert "psycopg" not in text.lower()

    def test_not_found_envelope(self, user_a: AuthedClient, app: FastAPI) -> None:
        body = user_a.get(f"/api/v1/documents/{uuid.uuid4()}").json()
        assert body["code"] == "resource_not_found"
        assert body["request_id"]
        self._assert_clean(body, app)

    def test_validation_envelope(self, user_a: AuthedClient, app: FastAPI) -> None:
        body = user_a.post("/api/v1/projects", json={"title": "x" * 5000}).json()
        self._assert_clean(body, app)

    def test_file_rejected_envelope_has_no_path(self, user_a: AuthedClient, app: FastAPI) -> None:
        body = user_a.upload(**upload_kwargs(make_corrupt_pdf(), "bad.pdf", PDF_MIME)).json()
        assert body["code"] == "file_rejected"
        self._assert_clean(body, app)
        # Only allow-listed detail keys are exposed.
        assert set(body["details"]) <= {"reason", "hint", "allowed", "max_pages", "max_bytes"}

    def test_request_id_present_on_errors(self, user_a: AuthedClient) -> None:
        response = user_a.get(f"/api/v1/documents/{uuid.uuid4()}")
        assert response.headers["x-request-id"]
        assert response.json()["request_id"] == response.headers["x-request-id"]

    def test_request_id_is_not_client_controllable_in_body(self, user_a: AuthedClient) -> None:
        payload = "x" * 4000
        response = user_a.get(
            f"/api/v1/documents/{uuid.uuid4()}", headers={"X-Request-Id": payload}
        )
        # Clamped and character-filtered, never echoed verbatim.
        assert len(response.headers["x-request-id"]) <= 64
        assert response.headers["x-request-id"] != payload

    def test_document_content_not_in_error_metadata(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        document_id = user_a.upload(
            **upload_kwargs(make_scanned_like_pdf(), "scan.pdf", PDF_MIME)
        ).json()["id"]
        run_worker(app)
        body = user_a.get(f"/api/v1/documents/{document_id}").json()
        assert body["status"] == "failed"
        assert body["error_code"] == "empty_extraction_scanned"
        assert len(body["error_message"]) < 500
        self._assert_clean(body, app)

    def test_no_html_injection_in_extracted_text(self, user_a: AuthedClient, app: FastAPI) -> None:
        """Extracted text is returned as JSON text, not rendered HTML."""
        from tests.factories.pdf_factory import build_pdf

        payload = build_pdf([["<script>alert(1)</script>", "<img src=x onerror=alert(1)>"]])
        document_id = user_a.upload(**upload_kwargs(payload, "xss.pdf", PDF_MIME)).json()["id"]
        run_worker(app)
        response = user_a.get(f"/api/v1/documents/{document_id}/text")
        assert response.headers["content-type"].startswith("application/json")
        items = response.json()["items"]
        assert items
        # The payload survives verbatim as *data*: nothing sanitized it, and
        # nothing interpreted it. Safe rendering is the client's job, which is
        # why the API returns JSON and never HTML.
        recovered = " ".join(item["text"] for item in items)
        assert "script" in recovered
        assert "onerror" in recovered
        # The response is not an HTML document, and framing/scripting is denied.
        assert "text/html" not in response.headers["content-type"]
        assert response.headers["content-security-policy"] == (
            "default-src 'none'; frame-ancestors 'none'"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
