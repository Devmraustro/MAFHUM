"""Backup and restore, verified by actually restoring.

The requirement that matters here: a backup command completing successfully is
not evidence that recovery works. So every test in this module performs a real
restore into a scratch database and blob directory, then checks that the restored
system is internally consistent -- every document row's ``storage_key`` resolves
to a real blob with the recorded checksum.

These tests run against a real PostgreSQL and the real ``pg_dump``/``pg_restore``
bundled with pgserver. What they prove is the backup format, the restore path and
the consistency check. They do not prove that a production operator's environment
has compatible client tools.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from tools.backup import (
    create_backup,
    find_pg_tools,
    read_manifest,
    restore_backup,
    verify_backup,
    verify_state,
)

from tests.conftest import database_url_with_name, run_worker, upload_kwargs
from tests.fixtures_content import make_pdf

PDF_MIME = "application/pdf"


@pytest.fixture
def scratch_targets(postgres_url: str, tmp_path: Path) -> dict[str, object]:
    """A separate database and blob directory to restore into."""
    from sqlalchemy import create_engine, text

    name = "mafhum_restore_test"
    # The pgserver URI shape differs between POSIX (Unix socket + ?host= query)
    # and Windows (plain TCP). A literal `.replace("/mafhum_test?", ...)` used
    # to no-op on Windows and point the restore drill at the wrong database.
    admin = database_url_with_name(postgres_url, "postgres").replace("+psycopg", "")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).scalar()
        if exists:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()

    target_url = database_url_with_name(postgres_url, name)
    return {"database_url": target_url, "storage_root": tmp_path / "restored-blobs"}


def _seed(app: FastAPI, user) -> list[str]:
    """Upload documents, process them, and return their storage keys."""
    from sqlalchemy import select

    from mafhum_api.db.models import Document

    keys: list[str] = []
    for index in range(3):
        response = user.post(
            "/api/v1/documents",
            **upload_kwargs(make_pdf("arabic", pages=1), f"seed-{index}.pdf", PDF_MIME),
        )
        assert response.status_code in (200, 201), response.text
    run_worker(app)
    with app.state.context.database.session() as db:
        keys = [str(k) for k in db.scalars(select(Document.storage_key)).all()]
    assert keys, "no documents were created"
    return keys


class TestToolingAvailability:
    def test_pg_tools_are_locatable(self, postgres_url: str) -> None:
        dump, restore = find_pg_tools(postgres_url)
        assert Path(dump).exists(), dump
        assert Path(restore).exists(), restore


class TestCreateBackup:
    def test_archive_contains_database_blobs_and_manifest(
        self, app: FastAPI, user_a, clean_tables: None, tmp_path: Path
    ) -> None:
        _seed(app, user_a)
        archive = create_backup(
            database_url=app.state.context.settings.database_url,
            storage_root=Path(app.state.context.settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        assert archive.exists()
        with tarfile.open(archive, "r") as tar:
            names = {m.name.split("/")[0] for m in tar.getmembers()}
        assert {"database.dump", "manifest.json", "blobs"} <= names

    def test_manifest_lists_every_blob_with_a_checksum(
        self, app: FastAPI, user_a, clean_tables: None, tmp_path: Path
    ) -> None:
        keys = _seed(app, user_a)
        archive = create_backup(
            database_url=app.state.context.settings.database_url,
            storage_root=Path(app.state.context.settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        manifest = read_manifest(archive)
        assert manifest["blob_count"] == len(keys)
        entries = manifest["blobs"]
        assert len(entries) == len(keys)
        for entry in entries:
            assert len(str(entry["sha256"])) == 64
            assert int(entry["size"]) > 0

    def test_manifest_contains_no_credentials(
        self, app: FastAPI, user_a, clean_tables: None, tmp_path: Path
    ) -> None:
        """The archive is portable; it must not carry a connection string."""
        _seed(app, user_a)
        archive = create_backup(
            database_url=app.state.context.settings.database_url,
            storage_root=Path(app.state.context.settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        manifest = read_manifest(archive)
        serialized = json.dumps(manifest)
        assert "postgres" not in serialized.lower(), "connection scheme leaked into the manifest"
        assert "://" not in serialized
        # Only the database name is recorded.
        assert manifest["database_name"] == "mafhum_test"

    def test_quiescing_is_recorded_not_assumed(
        self, app: FastAPI, user_a, clean_tables: None, tmp_path: Path
    ) -> None:
        """Whether the app was stopped is a fact about the backup, so it is stored."""
        _seed(app, user_a)
        settings = app.state.context.settings
        live = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "live",
        )
        quiesced = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "quiesced",
            require_quiesced=True,
        )
        assert read_manifest(live)["quiesced_asserted"] is False
        assert read_manifest(quiesced)["quiesced_asserted"] is True


class TestRestore:
    def test_restoring_reproduces_the_documents(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        """The core claim: a restored database holds the same documents."""
        from sqlalchemy import create_engine, text

        keys = _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )

        result = restore_backup(
            archive=archive,
            database_url=str(scratch_targets["database_url"]),
            storage_root=Path(str(scratch_targets["storage_root"])),
        )
        assert result["blobs_restored"] == len(keys)

        engine = create_engine(str(scratch_targets["database_url"]).replace("+psycopg", ""))
        try:
            with engine.connect() as connection:
                restored = {
                    str(row[0])
                    for row in connection.execute(
                        text("SELECT storage_key FROM documents WHERE storage_key IS NOT NULL")
                    ).all()
                }
                emails = {
                    str(row[0]) for row in connection.execute(text("SELECT email FROM users")).all()
                }
        finally:
            engine.dispose()

        assert restored == set(keys), "restored documents do not match the source"
        assert emails == {"student.a@example.com"}, "accounts were not restored"

    def test_restored_blobs_match_their_recorded_checksums(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        """A blob restored byte-for-byte wrong is worse than a missing one."""
        _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        storage = Path(str(scratch_targets["storage_root"]))
        restore_backup(
            archive=archive,
            database_url=str(scratch_targets["database_url"]),
            storage_root=storage,
        )

        manifest = read_manifest(archive)
        import hashlib

        for entry in manifest["blobs"]:
            blob = storage / str(entry["key"])
            assert blob.exists(), f"blob {entry['key']} was not restored"
            assert hashlib.sha256(blob.read_bytes()).hexdigest() == entry["sha256"]


class TestVerificationDetectsDamage:
    """The consistency check must fail loudly, or it is decoration."""

    def test_verify_passes_on_an_intact_backup(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        keys = _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        report = verify_backup(
            archive=archive,
            database_url=str(scratch_targets["database_url"]),
            storage_root=Path(str(scratch_targets["storage_root"])),
        )
        assert report["ok"] is True, report["problems"]
        assert report["documents_in_restored_database"] == len(keys)
        assert report["missing_blobs"] == []
        assert report["checksum_mismatches"] == []

    def test_verify_fails_when_a_blob_is_missing_from_the_archive(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        """A database restored without its blobs must be reported as broken."""
        _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )

        # Rebuild the archive without the blobs directory.
        staging = tmp_path / "repack"
        staging.mkdir()
        with tarfile.open(archive, "r") as tar:
            from tools.backup import _safe_extractall

            _safe_extractall(tar, staging)
        (staging / "blobs").rmdir() if not any((staging / "blobs").iterdir()) else None
        import shutil

        shutil.rmtree(staging / "blobs", ignore_errors=True)
        damaged = tmp_path / "damaged.tar"
        with tarfile.open(damaged, "w") as tar:
            for name in ("database.dump", "manifest.json"):
                tar.add(staging / name, arcname=name)

        report = verify_backup(
            archive=damaged,
            database_url=str(scratch_targets["database_url"]),
            storage_root=Path(str(scratch_targets["storage_root"])),
        )
        assert report["ok"] is False
        assert any("absent from the archive" in p for p in report["problems"]), report["problems"]
        assert report["missing_blobs"], "missing blobs were not named"

    def test_verify_fails_when_a_blob_is_corrupted(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        """Checksum verification has to catch a silently wrong file."""
        _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        storage = Path(str(scratch_targets["storage_root"]))
        restore_backup(
            archive=archive,
            database_url=str(scratch_targets["database_url"]),
            storage_root=storage,
        )
        victim = next(p for p in storage.rglob("*") if p.is_file())
        victim.write_bytes(b"corrupted")

        # verify_state, not verify_backup: verify_backup restores first and would
        # silently overwrite the corruption it was meant to find. This is the
        # check an operator runs against a live system after a real restore.
        report = verify_state(
            database_url=str(scratch_targets["database_url"]),
            storage_root=storage,
            manifest=read_manifest(archive),
        )
        assert report["ok"] is False
        assert report["checksum_mismatches"], "corruption was not detected"

    def test_a_tampered_dump_is_rejected_before_restore(
        self,
        app: FastAPI,
        user_a,
        clean_tables: None,
        tmp_path: Path,
        scratch_targets: dict[str, object],
    ) -> None:
        """The dump is checksummed against the manifest, so edits are caught."""
        _seed(app, user_a)
        settings = app.state.context.settings
        archive = create_backup(
            database_url=settings.database_url,
            storage_root=Path(settings.storage_root),
            out_dir=tmp_path / "backups",
        )
        staging = tmp_path / "repack2"
        staging.mkdir()
        with tarfile.open(archive, "r") as tar:
            from tools.backup import _safe_extractall

            _safe_extractall(tar, staging)
        dump = staging / "database.dump"
        dump.write_bytes(dump.read_bytes() + b"tampered")
        tampered = tmp_path / "tampered.tar"
        with tarfile.open(tampered, "w") as tar:
            for name in ("database.dump", "manifest.json"):
                tar.add(staging / name, arcname=name)
            if (staging / "blobs").exists():
                tar.add(staging / "blobs", arcname="blobs")

        with pytest.raises(RuntimeError, match="checksum does not match"):
            restore_backup(
                archive=tampered,
                database_url=str(scratch_targets["database_url"]),
                storage_root=Path(str(scratch_targets["storage_root"])),
            )

    def test_an_archive_with_a_traversal_path_is_refused(
        self, tmp_path: Path, scratch_targets: dict[str, object]
    ) -> None:
        """Extracting a hostile archive must not write outside the staging dir."""
        evil = tmp_path / "evil.tar"
        payload = tmp_path / "payload"
        payload.write_text("x")
        with tarfile.open(evil, "w") as tar:
            info = tar.gettarinfo(str(payload), arcname="../../etc/mafhum-evil")
            with payload.open("rb") as handle:
                tar.addfile(info, handle)
        with pytest.raises(RuntimeError, match="unsafe path"):
            restore_backup(
                archive=evil,
                database_url=str(scratch_targets["database_url"]),
                storage_root=Path(str(scratch_targets["storage_root"])),
            )
