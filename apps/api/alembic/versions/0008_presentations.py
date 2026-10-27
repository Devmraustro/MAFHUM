"""Editable presentation jobs and private PPTX artifacts.

Revision ID: 0008_presentations
Revises: 0007_document_scan_verdict
Create Date: 2026-10-27

Why
---
Phase 6 needs a durable record of what a deck was built from, not just the file.
``presentation_jobs.spec`` stores the exact bounded deck spec (including citation
locators and document ids) so source availability can be recomputed on read
after a document is deleted. The artifact row points at an opaque storage key;
no filesystem path or user-supplied filename is ever persisted as a locator.

Notes for operators
-------------------
* Purely additive. Existing rows are untouched and no backfill is required.
* Both tables cascade from ``users`` at the database level, but the application
  deletes artifact blobs *before* the account row (see DeletionService); a
  cascade alone would leave orphaned bytes on disk.
* The downgrade drops both tables and therefore the generation history. Any
  blobs left behind must be reclaimed with the storage reconciliation job.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_presentations"
down_revision: str | None = "0007_document_scan_verdict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "presentation_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False, server_default="ar"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("slide_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generator_name", sa.String(length=64), nullable=True),
        sa.Column("generator_version", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_presentation_jobs_status",
        ),
        sa.CheckConstraint("language IN ('ar', 'fr')", name="ck_presentation_jobs_language"),
        sa.CheckConstraint("slide_count >= 0", name="ck_presentation_jobs_slide_count"),
        sa.CheckConstraint("citation_count >= 0", name="ck_presentation_jobs_citation_count"),
        sa.CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 300",
            name="ck_presentation_jobs_error_len",
        ),
    )
    op.create_index("ix_presentation_jobs_owner", "presentation_jobs", ["owner_id", "created_at"])
    op.create_index("ix_presentation_jobs_owner_id", "presentation_jobs", ["owner_id"])

    op.create_table(
        "presentation_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("presentation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=128), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False, server_default="pptx"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("storage_key", name="uq_presentation_artifacts_storage_key"),
        sa.UniqueConstraint("job_id", name="uq_presentation_artifacts_job"),
        sa.CheckConstraint("size_bytes > 0", name="ck_presentation_artifacts_size"),
        sa.CheckConstraint("format = 'pptx'", name="ck_presentation_artifacts_format"),
    )
    op.create_index(
        "ix_presentation_artifacts_owner", "presentation_artifacts", ["owner_id", "created_at"]
    )
    op.create_index("ix_presentation_artifacts_owner_id", "presentation_artifacts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_presentation_artifacts_owner_id", table_name="presentation_artifacts")
    op.drop_index("ix_presentation_artifacts_owner", table_name="presentation_artifacts")
    op.drop_table("presentation_artifacts")
    op.drop_index("ix_presentation_jobs_owner_id", table_name="presentation_jobs")
    op.drop_index("ix_presentation_jobs_owner", table_name="presentation_jobs")
    op.drop_table("presentation_jobs")
