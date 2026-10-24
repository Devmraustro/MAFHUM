"""Phase 5 integration: research results saved into a study topic.

Revision ID: 0005_saved_research
Revises: 0004_study
Create Date: 2026-10-23

Notes for operators
-------------------
* Purely additive; the downgrade drops one table and loses saved research only.
* ``source_document_ids`` is a JSONB array rather than a foreign key on purpose.
  A saved result must survive the deletion of the document it came from -- that
  is the snapshot half of the design -- so there is nothing to cascade.
  Availability is recomputed on read instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_saved_research"
down_revision: str | None = "0004_study"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_research",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_sufficient", sa.Boolean(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("citations", JSONB(), nullable=False),
        sa.Column("source_document_ids", JSONB(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deduplicated", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('grounded', 'insufficient_evidence', 'no_evidence_available')",
            name="ck_saved_research_status",
        ),
    )
    op.create_index("ix_saved_research_topic", "saved_research", ["topic_id", "created_at"])
    op.create_index("ix_saved_research_owner", "saved_research", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_research_owner", table_name="saved_research")
    op.drop_index("ix_saved_research_topic", table_name="saved_research")
    op.drop_table("saved_research")
