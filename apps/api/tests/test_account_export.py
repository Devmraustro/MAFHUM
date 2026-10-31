"""Account data export: isolation, path safety, bounded streaming and audit."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mafhum_api.db.models import AuditEvent, Document, User
from mafhum_api.main import create_app
from mafhum_api.services.account.export import AccountExportService, ExportLimits
from tests.conftest import AuthedClient, _register, upload_kwargs
from tests.factories.pdf_factory import build_pdf

PDF_MIME = "application/pdf"


def _pdf(text: str) -> bytes:
    return build_pdf([[text, "سطر ثانٍ للاختبار."]])


def _upload(user: AuthedClient, name: str, text: str) -> None:
    response = user.post(
        "/api/v1/documents",
        **upload_kwargs(_pdf(text), name, PDF_MIME),
    )
    assert response.status_code == 201, response.text


def _zip(response) -> zipfile.ZipFile:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment;" in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]
    return zipfile.ZipFile(io.BytesIO(response.content))


class TestAuthenticationAndShape:
    def test_export_requires_a_session(self, client: TestClient) -> None:
        response = client.get("/api/v1/me/export")
        assert response.status_code == 401, response.text

    def test_empty_account_still_produces_a_readable_archive(self, user_a: AuthedClient) -> None:
        with _zip(user_a.get("/api/v1/me/export")) as archive:
            names = set(archive.namelist())
            assert {
                "README.txt",
                "account.json",
                "manifest.json",
                "projects.json",
                "documents.json",
                "sources.json",
                "presentations.json",
                "study_topics.json",
            } <= names
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["counts"]["files"] == 0
            assert manifest["truncated"] is False
            assert "password_hash" in manifest["excluded_by_design"]

    def test_no_password_hash_or_session_data_leaks(self, user_a: AuthedClient) -> None:
        with _zip(user_a.get("/api/v1/me/export")) as archive:
            blob = b"".join(archive.read(name) for name in archive.namelist())
            assert b"scrypt$" not in blob
            assert user_a.password.encode() not in blob
            account = json.loads(archive.read("account.json"))
            assert "password_hash" not in account
            assert "token_hash" not in account
            # The manifest may NAME excluded fields; that is not a value leak.
            assert set(account) == {
                "id",
                "email",
                "display_name",
                "preferred_language",
                "role",
                "created_at",
            }


class TestOwnershipIsolation:
    def test_another_users_blob_never_appears(
        self, two_users: tuple[AuthedClient, AuthedClient]
    ) -> None:
        user_a, user_b = two_users
        bytes_a = _pdf("محتوى خاص بالمستخدم الأول فقط")
        bytes_b = _pdf("محتوى سري للمستخدم الثاني يجب ألا يظهر")
        response_a = user_a.post("/api/v1/documents", **upload_kwargs(bytes_a, "a.pdf", PDF_MIME))
        response_b = user_b.post("/api/v1/documents", **upload_kwargs(bytes_b, "b.pdf", PDF_MIME))
        assert response_a.status_code == 201, response_a.text
        assert response_b.status_code == 201, response_b.text

        with _zip(user_a.get("/api/v1/me/export")) as archive:
            all_bytes = b"".join(
                archive.read(name) for name in archive.namelist() if name.startswith("files/")
            )
            assert bytes_a in all_bytes
            assert bytes_b not in all_bytes
            manifest = json.loads(archive.read("manifest.json"))
            assert [item["original_filename"] for item in manifest["files"]] == ["a.pdf"]
            account = json.loads(archive.read("account.json"))
            assert account["email"] == user_a.email
            assert user_b.email not in archive.read("account.json").decode()

    def test_export_cannot_be_used_as_a_cross_user_read_by_cookie(
        self, two_users: tuple[AuthedClient, AuthedClient]
    ) -> None:
        user_a, user_b = two_users
        _upload(user_b, "private-b.pdf", "بيانات المستخدم ب")
        with _zip(user_a.get("/api/v1/me/export")) as archive:
            assert json.loads(archive.read("documents.json")) == []
            assert not any(name.startswith("files/") for name in archive.namelist())


class TestArchivePathSafety:
    def test_user_controlled_filename_never_becomes_an_archive_path(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        _upload(user_a, "ordinary.pdf", "مستند عادي")
        with app.state.context.database.session() as db:
            document = db.scalars(select(Document)).one()
            document.display_filename = "../../../etc/evil/..\\secret name.pdf"
            db.commit()

        with _zip(user_a.get("/api/v1/me/export")) as archive:
            for name in archive.namelist():
                assert ".." not in name, name
                assert "\\" not in name, name
                assert not name.startswith("/"), name
                if name.startswith("files/"):
                    assert name.endswith((".pdf", ".docx", ".pptx", ".bin")), name
                    assert Path(name).parent.as_posix() == "files"
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["files"][0]["original_filename"].startswith("../../../")
            # Storage keys are server secrets; they must not be in the archive.
            assert all(
                "/" not in item["archive_name"].removeprefix("files/") for item in manifest["files"]
            )


class TestBoundsAndAudit:
    def test_service_stops_at_the_configured_total_byte_cap(
        self, user_a: AuthedClient, app: FastAPI, db
    ) -> None:  # type: ignore[no-untyped-def]
        _upload(user_a, "one.pdf", "المستند الأول")
        service = AccountExportService(
            app.state.context.storage,
            ExportLimits(
                max_files=10,
                max_file_bytes=10 * 1024 * 1024,
                max_total_bytes=1,
                spool_memory_bytes=1024,
                max_seconds=30.0,
            ),
        )
        user = db.get(User, user_a.user_id)
        assert user is not None
        result = service.build(db, user)
        try:
            with zipfile.ZipFile(result.stream) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["truncated"] is True
            assert "export_total_bytes_limit" in manifest["notices"]
            assert manifest["counts"]["files"] == 0
        finally:
            result.stream.close()

    def test_service_stops_at_the_configured_file_count(
        self, user_a: AuthedClient, app: FastAPI, db
    ) -> None:  # type: ignore[no-untyped-def]
        for index in range(3):
            _upload(user_a, f"doc-{index}.pdf", f"المستند رقم {index}")
        service = AccountExportService(
            app.state.context.storage,
            ExportLimits(
                max_files=2,
                max_file_bytes=10 * 1024 * 1024,
                max_total_bytes=100 * 1024 * 1024,
                spool_memory_bytes=1024,
                max_seconds=30.0,
            ),
        )
        user = db.get(User, user_a.user_id)
        assert user is not None
        result = service.build(db, user)
        try:
            with zipfile.ZipFile(result.stream) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["counts"]["files"] == 2
            assert "export_file_count_limit" in manifest["notices"]
        finally:
            result.stream.close()

    def test_endpoint_honours_small_export_settings_and_writes_an_audit_row(
        self, settings, clean_tables: None, tmp_path: Path
    ) -> None:
        custom = settings.model_copy(
            update={"export_max_files": 1, "storage_root": tmp_path / "export-store"}
        )
        Path(custom.storage_root).mkdir(parents=True, exist_ok=True)
        application = create_app(custom, start_worker=False)
        try:
            user = _register(application, "export.bounds@example.com", "Passw0rd!23")
            _upload(user, "a.pdf", "واحد")
            _upload(user, "b.pdf", "اثنان")
            response = user.get("/api/v1/me/export")
            assert response.status_code == 200, response.text
            assert response.headers["x-mafhum-export-truncated"] == "true"
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["counts"]["files"] == 1
            with application.state.context.database.session() as db:
                events = db.scalars(
                    select(AuditEvent).where(AuditEvent.action == "account_exported")
                ).all()
            assert len(events) == 1
            assert events[0].details == {"count": 1, "status": "truncated"}
            assert "export.bounds@example.com" not in str(events[0].details)
        finally:
            application.state.context.database.dispose()

    def test_a_query_parameter_cannot_select_another_account(
        self, two_users: tuple[AuthedClient, AuthedClient]
    ) -> None:
        user_a, user_b = two_users
        _upload(user_b, "private-b.pdf", "بيانات المستخدم ب")
        response = user_a.get(f"/api/v1/me/export?user_id={user_b.user_id}")
        with _zip(response) as archive:
            account = json.loads(archive.read("account.json"))
            assert account["email"] == user_a.email
            assert json.loads(archive.read("documents.json")) == []
