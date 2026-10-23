"""Alembic migration tests against a disposable PostgreSQL database.

These run the real upgrade and downgrade on a throwaway database, not SQLite:
the migrations use PostgreSQL-specific constructs (native UUID, ``~*`` regex
checks) that SQLite would silently accept or reject differently.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

EXPECTED_TABLES = {
    "users",
    "user_sessions",
    "projects",
    "documents",
    "document_processing_jobs",
    "document_text_segments",
    "source_records",
}

API_ROOT = Path(__file__).resolve().parents[1]


def _with_database(url: str, database: str) -> str:
    """Return ``url`` pointed at a different database, preserving query options.

    The test cluster is reached over a Unix socket, so the host lives in the query
    string (``?host=/tmp/...``) rather than in the netloc. Rebuilding the URL by
    string surgery on the path would drop it.
    """
    parsed = make_url(url)
    return str(parsed.set(database=database))


@pytest.fixture
def scratch_url(postgres_url: str) -> Iterator[str]:
    """Create a brand-new empty database, and drop it afterwards."""
    name = f"mafhum_migration_{uuid.uuid4().hex[:12]}"
    admin_url = _with_database(postgres_url, "postgres")
    target_url = _with_database(postgres_url, name)

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()

    yield target_url

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


def make_config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # ``set_main_option`` stores into a ConfigParser, which treats ``%`` as
    # interpolation. The socket host is percent-encoded, so escape it.
    config.set_main_option("sqlalchemy.url", url.replace("+psycopg", "").replace("%", "%%"))
    return config


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestUpgrade:
    def test_upgrade_creates_all_tables(self, scratch_url: str) -> None:
        command.upgrade(make_config(scratch_url), "head")
        created = table_names(scratch_url)
        assert created >= EXPECTED_TABLES

    def test_upgrade_is_idempotent_when_already_at_head(self, scratch_url: str) -> None:
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        command.upgrade(config, "head")  # no-op, must not raise
        assert table_names(scratch_url) >= EXPECTED_TABLES

    def test_version_table_records_head(self, scratch_url: str) -> None:
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
        finally:
            engine.dispose()
        head = ScriptDirectory.from_config(config).get_current_head()
        assert version == head


class TestConstraints:
    def test_check_constraints_exist(self, scratch_url: str) -> None:
        command.upgrade(make_config(scratch_url), "head")
        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE contype = 'c' AND conrelid = 'documents'::regclass"
                    )
                ).fetchall()
        finally:
            engine.dispose()
        names = {row[0] for row in rows}
        assert "ck_documents_size_positive" in names
        assert "ck_documents_status" in names
        assert "ck_documents_format" in names

    def test_indexes_exist(self, scratch_url: str) -> None:
        command.upgrade(make_config(scratch_url), "head")
        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'documents'")
                ).fetchall()
        finally:
            engine.dispose()
        names = {row[0] for row in rows}
        assert "ix_documents_owner_created" in names
        assert "ix_documents_owner_status" in names
        assert "uq_documents_storage_key" in names

    def test_regex_checks_are_postgres_specific_and_active(self, scratch_url: str) -> None:
        """The DOI/URL checks use ``~*``, which only exists on PostgreSQL."""
        command.upgrade(make_config(scratch_url), "head")
        engine = create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                owner = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, display_name, password_hash) "
                        "VALUES (:id, :email, 't', :hash)"
                    ),
                    {"id": owner, "email": "mig@example.com", "hash": "x"},
                )
                # A malformed DOI must be rejected by the DB, not just the API.
                with pytest.raises(Exception) as info:
                    connection.execute(
                        text(
                            "INSERT INTO source_records (id, owner_id, title, doi) "
                            "VALUES (:id, :owner, 't', :doi)"
                        ),
                        {"id": uuid.uuid4(), "owner": owner, "doi": "not-a-doi"},
                    )
                assert "ck_sources_doi_shape" in str(info.value)
        finally:
            engine.dispose()


class TestDowngrade:
    def test_full_downgrade_removes_phase3_tables(self, scratch_url: str) -> None:
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        command.downgrade(config, "0001_identity")
        remaining = table_names(scratch_url)
        assert "documents" not in remaining
        assert "document_text_segments" not in remaining
        assert "document_processing_jobs" not in remaining
        assert "source_records" not in remaining
        # Phase 1/2 tables survive.
        assert {"users", "user_sessions", "projects"} <= remaining

    def test_downgrade_to_base_removes_everything(self, scratch_url: str) -> None:
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        # ``alembic_version`` is Alembic's own bookkeeping table and intentionally
        # survives; every application table must be gone.
        assert table_names(scratch_url) == {"alembic_version"}

    def test_upgrade_downgrade_upgrade_round_trip(self, scratch_url: str) -> None:
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        assert table_names(scratch_url) >= EXPECTED_TABLES

    def test_identity_data_survives_phase3_downgrade(self, scratch_url: str) -> None:
        """Phase 3 downgrade must not destroy Phase 1/2 data."""
        config = make_config(scratch_url)
        command.upgrade(config, "head")
        engine = create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, display_name, password_hash) "
                        "VALUES (:id, :email, 'طالب', :hash)"
                    ),
                    {"id": uuid.uuid4(), "email": "keep@example.com", "hash": "x"},
                )
        finally:
            engine.dispose()

        command.downgrade(config, "0001_identity")
        command.upgrade(config, "head")

        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                count = connection.execute(text("SELECT count(*) FROM users")).scalar()
        finally:
            engine.dispose()
        assert count == 1


class TestHistory:
    def test_single_linear_head(self) -> None:
        config = make_config("postgresql://unused")
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        assert len(heads) == 1

    def test_down_revision_chain_is_linear(self) -> None:
        config = make_config("postgresql://unused")
        script = ScriptDirectory.from_config(config)
        revisions = list(script.walk_revisions("base", "heads"))
        # walk_revisions returns newest-first. Asserting the exact chain, rather
        # than just its length, catches both a gap and a duplicated revision.
        assert [r.revision for r in revisions] == [
            "0005_saved_research",
            "0004_study",
            "0003_source_dedup",
            "0002_documents",
            "0001_identity",
        ]
        assert revisions[-1].down_revision is None, "the base revision must have no parent"
        # Every link must point at the revision immediately before it, so a
        # skipped or duplicated revision fails here rather than only at deploy
        # time.
        for newer, older in pairwise(revisions):
            assert newer.down_revision == older.revision, (
                f"{newer.revision} claims parent {newer.down_revision}, expected {older.revision}"
            )
