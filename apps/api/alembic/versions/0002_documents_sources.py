"""Documents, processing jobs, text segments and source catalog.

Revision ID: 0002_documents
Revises: 0001_identity
Create Date: 2026-10-21

Notes for operators
-------------------
* Purely additive: no Phase 1/2 table is altered or dropped, so the downgrade is
  lossless for identity data.
* The ``source_records`` DOI/URL checks use the PostgreSQL ``~*`` regex operator
  and are therefore PostgreSQL-specific by design. SQLite is supported for fast
  unit tests via ``metadata.create_all``, not via these migrations.
* Downgrading drops ``documents`` and its dependents, which destroys extracted
  text. The stored blobs are NOT deleted by this downgrade; run the storage
  cleanup command separately if you intend to reclaim disk space.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_documents"
down_revision: str | None = "0001_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("storage_key", sa.String(length=128), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("deletion_state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("language_hint", sa.String(length=8), nullable=True),
        sa.Column("is_scanned", sa.Boolean(), nullable=True),
        sa.Column("parser_name", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_documents_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_documents_project", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        sa.CheckConstraint("size_bytes > 0", name="ck_documents_size_positive"),
        sa.CheckConstraint("char_count >= 0", name="ck_documents_char_count_non_negative"),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_documents_page_count"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')", name="ck_documents_status"
        ),
        sa.CheckConstraint("format IN ('pdf', 'docx')", name="ck_documents_format"),
        sa.CheckConstraint(
            "deletion_state IN ('active', 'pending_deletion', 'deleted')",
            name="ck_documents_deletion_state",
        ),
        sa.CheckConstraint("segment_count >= 0", name="ck_documents_segment_count"),
        sa.CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="ck_documents_error_message_len",
        ),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_owner_created", "documents", ["owner_id", "created_at"])
    op.create_index("ix_documents_owner_status", "documents", ["owner_id", "status"])

    op.create_table(
        "document_processing_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_jobs_document",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "generation", name="uq_jobs_document_generation"),
        sa.CheckConstraint("generation >= 1", name="ck_jobs_generation_positive"),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts_non_negative"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
    )
    op.create_index(
        "ix_document_processing_jobs_document_id", "document_processing_jobs", ["document_id"]
    )
    op.create_index("ix_jobs_status_queued_at", "document_processing_jobs", ["status", "queued_at"])

    op.create_table(
        "document_text_segments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_segments_document",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_segments_document_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_segments_ordinal_non_negative"),
        sa.CheckConstraint("char_count > 0", name="ck_segments_char_count_positive"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="ck_segments_page_number"
        ),
        sa.CheckConstraint("generation >= 1", name="ck_segments_generation_positive"),
    )
    op.create_index(
        "ix_document_text_segments_document_id", "document_text_segments", ["document_id"]
    )
    op.create_index(
        "ix_segments_document_page", "document_text_segments", ["document_id", "page_number"]
    )

    op.create_table(
        "source_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("authors", JSONB(), nullable=True),
        sa.Column("institution", sa.String(length=255), nullable=True),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column(
            "source_type", sa.String(length=24), nullable=False, server_default="user_upload"
        ),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("isbn", sa.String(length=32), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("repository_identifier", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "provenance", sa.String(length=24), nullable=False, server_default="user_supplied"
        ),
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("verified_by", sa.String(length=120), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parser_name", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_sources_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_sources_document",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "publication_year IS NULL OR (publication_year BETWEEN 1000 AND 2100)",
            name="ck_sources_year_range",
        ),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_sources_page_count"),
        sa.CheckConstraint(
            "source_type IN ('user_upload', 'lecture_notes', 'book', 'journal_article',"
            " 'thesis', 'report', 'legal_text', 'website', 'other')",
            name="ck_sources_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('user_supplied', 'document_extracted', 'external_verified')",
            name="ck_sources_provenance",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'user_asserted', 'verified')",
            name="ck_sources_verification",
        ),
        sa.CheckConstraint(
            "doi IS NULL OR doi ~* '^10\\.\\d{4,9}/\\S+$'", name="ck_sources_doi_shape"
        ),
        sa.CheckConstraint("url IS NULL OR url ~* '^https?://'", name="ck_sources_url_scheme"),
        sa.CheckConstraint(
            "verification_status <> 'verified' OR verified_by IS NOT NULL",
            name="ck_sources_verified_requires_source",
        ),
    )
    op.create_index("ix_source_records_owner_id", "source_records", ["owner_id"])
    op.create_index("ix_source_records_document_id", "source_records", ["document_id"])
    op.create_index("ix_sources_owner_created", "source_records", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_sources_owner_created", table_name="source_records")
    op.drop_index("ix_source_records_document_id", table_name="source_records")
    op.drop_index("ix_source_records_owner_id", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_segments_document_page", table_name="document_text_segments")
    op.drop_index("ix_document_text_segments_document_id", table_name="document_text_segments")
    op.drop_table("document_text_segments")
    op.drop_index("ix_jobs_status_queued_at", table_name="document_processing_jobs")
    op.drop_index("ix_document_processing_jobs_document_id", table_name="document_processing_jobs")
    op.drop_table("document_processing_jobs")
    op.drop_index("ix_documents_owner_status", table_name="documents")
    op.drop_index("ix_documents_owner_created", table_name="documents")
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_table("documents")
