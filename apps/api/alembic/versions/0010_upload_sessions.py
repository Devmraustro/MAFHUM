"""Chunked upload sessions for large documents.

Revision ID: 0010_upload_sessions
Revises: 0009_admin_audit
Create Date: 2026-11-01

Why
---
Vercel Functions cap a single request body at 4.5 MB while MAFHUM accepts
documents up to 20 MB. The Python Blob SDK offers no client-upload-token
minting (verified against the pinned ``vercel-blob==0.5.0``), so a browser
cannot upload directly to Blob with a scoped token from this backend. The
compatible flow is: the client creates an upload *intent*, uploads bounded
parts through the API (each request stays under the platform limit), and the
server assembles the final object from exactly the parts recorded for that
session — the client never chooses or learns any storage key.

Notes for operators
-------------------
* Purely additive: two new tables, no changes to existing ones, no backfill.
* Part rows reference server-generated opaque storage keys; the reconcile
  job must treat keys that belong to neither documents, presentation
  artifacts, nor live upload sessions as orphans.
* The downgrade drops session history. Any stored part objects or assembled
  objects without a document row must be reclaimed via storage
  reconciliation after downgrading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_upload_sessions"
down_revision: str | None = "0009_admin_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_upload_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("storage_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("declared_bytes", sa.BigInteger(), nullable=False),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("total_parts", sa.Integer(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("language_hint", sa.String(length=8), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'assembled', 'expired')",
            name="ck_upload_sessions_state",
        ),
        sa.CheckConstraint("declared_bytes > 0", name="ck_upload_sessions_declared_positive"),
        sa.CheckConstraint("part_size > 0", name="ck_upload_sessions_part_size_positive"),
        sa.CheckConstraint("total_parts > 0", name="ck_upload_sessions_total_parts_positive"),
        sa.CheckConstraint(
            "received_bytes >= 0 AND received_bytes <= declared_bytes",
            name="ck_upload_sessions_received_within_declared",
        ),
        sa.CheckConstraint("format IN ('pdf', 'docx')", name="ck_upload_sessions_format"),
    )
    op.create_index(
        "ix_upload_sessions_owner_created",
        "document_upload_sessions",
        ["owner_id", "created_at"],
    )
    op.create_index(
        "ix_upload_sessions_state_expires",
        "document_upload_sessions",
        ["state", "expires_at"],
    )

    op.create_table(
        "document_upload_parts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_upload_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", "part_number", name="uq_upload_parts_session_number"),
        sa.CheckConstraint("part_number > 0", name="ck_upload_parts_number_positive"),
        sa.CheckConstraint("size_bytes > 0", name="ck_upload_parts_size_positive"),
    )
    op.create_index("ix_upload_parts_session", "document_upload_parts", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_parts_session", table_name="document_upload_parts")
    op.drop_table("document_upload_parts")
    op.drop_index("ix_upload_sessions_state_expires", table_name="document_upload_sessions")
    op.drop_index("ix_upload_sessions_owner_created", table_name="document_upload_sessions")
    op.drop_table("document_upload_sessions")
