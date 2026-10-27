"""Admin role constraint and append-only audit events.

Revision ID: 0009_admin_audit
Revises: 0008_presentations
Create Date: 2026-10-27

Why
---
Phase 7 needs a privileged read-only view of the system. A role stored as a free
string is not a control: constrain it at the database level so a bad write cannot
create an undocumented role. Privilege is granted by an operator CLI against the
database, never by an HTTP endpoint, so there is no self-service escalation path.

Audit events are append-only at the API level. ``actor_id`` uses ON DELETE SET
NULL so account deletion keeps the event but drops the dangling reference.

Notes for operators
-------------------
* Additive: a nullable column-less table plus one CHECK on ``users.role``.
  Every existing row already has ``role='student'``, so the constraint needs no
  backfill and takes no table rewrite on PostgreSQL 16.
* The downgrade drops the audit table and therefore the recorded history. Take a
  backup first. Downgrading is a rollback of schema, not of evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_admin_audit"
down_revision: str | None = "0008_presentations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint("ck_users_role", "users", "role IN ('student', 'admin')")
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="success"),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("char_length(action) BETWEEN 1 AND 64", name="ck_audit_action_len"),
        sa.CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="ck_audit_outcome"),
        sa.CheckConstraint(
            "target_id IS NULL OR char_length(target_id) <= 64",
            name="ck_audit_target_len",
        ),
        sa.CheckConstraint(
            "request_id IS NULL OR char_length(request_id) <= 64",
            name="ck_audit_request_len",
        ),
    )
    op.create_index("ix_audit_events_created", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action", "created_at"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_constraint("ck_users_role", "users", type_="check")
