"""Phase 5 tutoring: study topics, sessions, questions, flashcards, plans.

Revision ID: 0004_study
Revises: 0003_source_dedup
Create Date: 2026-10-23

Notes for operators
-------------------
* Purely additive. No Phase 1-4 table is altered, so the downgrade is lossless for
  identity, document and source data -- it drops only the tutoring tables.
* Downgrading destroys study history (sessions, answers, flashcard review
  history, plans). Take a dump of the ``study_*``, ``flashcard*`` tables first if
  that matters.
* Foreign keys into ``documents``, ``source_records`` and
  ``document_text_segments`` use ``ON DELETE SET NULL`` deliberately. A study
  artifact must not silently start pointing at a different source when its own is
  deleted, and it must not disappear either: it stays and reports the reference
  as unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004_study"
down_revision: str | None = "0003_source_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "study_topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("language IN ('ar', 'fr')", name="ck_study_topics_language"),
    )
    op.create_index("ix_study_topics_owner", "study_topics", ["owner_id", "created_at"])

    op.create_table(
        "study_topic_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["source_records.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "document_id", "source_id", name="uq_materials_topic_target"),
        # At most one, not exactly one: ON DELETE SET NULL leaves both NULL once
        # the target is gone, and an exactly-one check would reject that UPDATE.
        sa.CheckConstraint(
            "NOT (document_id IS NOT NULL AND source_id IS NOT NULL)",
            name="ck_materials_at_most_one_target",
        ),
    )
    op.create_index("ix_materials_topic", "study_topic_materials", ["topic_id"])

    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_sufficient", sa.Boolean(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("citations", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('grounded', 'insufficient_evidence', 'no_evidence_available')",
            name="ck_sessions_status",
        ),
    )
    op.create_index("ix_sessions_topic", "study_sessions", ["topic_id", "created_at"])

    op.create_table(
        "study_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("source_segment_id", sa.Uuid(), nullable=True),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column("source_ordinal", sa.Integer(), nullable=True),
        sa.Column("answer_submitted", sa.Boolean(), nullable=False),
        sa.Column("submitted_answer", sa.Text(), nullable=True),
        sa.Column("feedback", JSONB(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_segment_id"], ["document_text_segments.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "difficulty IN ('recall', 'comprehension', 'application')",
            name="ck_questions_difficulty",
        ),
    )
    op.create_index("ix_questions_topic", "study_questions", ["topic_id", "created_at"])

    op.create_table(
        "flashcards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("source_title", sa.String(length=300), nullable=True),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column("source_ordinal", sa.Integer(), nullable=True),
        sa.Column("source_available", sa.Boolean(), nullable=False),
        sa.Column("ease_factor", sa.Float(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("ease_factor >= 1.3", name="ck_flashcards_ease_floor"),
        sa.CheckConstraint("interval_days >= 0", name="ck_flashcards_interval"),
        sa.CheckConstraint("repetitions >= 0", name="ck_flashcards_repetitions"),
    )
    op.create_index("ix_flashcards_topic_due", "flashcards", ["topic_id", "due_at"])

    op.create_table(
        "flashcard_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flashcard_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("interval_before_days", sa.Integer(), nullable=False),
        sa.Column("interval_after_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["flashcard_id"], ["flashcards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rating BETWEEN 0 AND 5", name="ck_reviews_rating"),
    )
    op.create_index("ix_reviews_card", "flashcard_reviews", ["flashcard_id", "created_at"])

    op.create_table(
        "study_plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("material_note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["study_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "state IN ('not_started', 'in_progress', 'completed', 'blocked')",
            name="ck_plan_items_state",
        ),
        sa.CheckConstraint(
            "state <> 'completed' OR completed_at IS NOT NULL", name="ck_plan_items_completed_at"
        ),
    )
    op.create_index("ix_plan_items_topic", "study_plan_items", ["topic_id", "scheduled_for"])


def downgrade() -> None:
    op.drop_index("ix_plan_items_topic", table_name="study_plan_items")
    op.drop_table("study_plan_items")
    op.drop_index("ix_reviews_card", table_name="flashcard_reviews")
    op.drop_table("flashcard_reviews")
    op.drop_index("ix_flashcards_topic_due", table_name="flashcards")
    op.drop_table("flashcards")
    op.drop_index("ix_questions_topic", table_name="study_questions")
    op.drop_table("study_questions")
    op.drop_index("ix_sessions_topic", table_name="study_sessions")
    op.drop_table("study_sessions")
    op.drop_index("ix_materials_topic", table_name="study_topic_materials")
    op.drop_table("study_topic_materials")
    op.drop_index("ix_study_topics_owner", table_name="study_topics")
    op.drop_table("study_topics")
