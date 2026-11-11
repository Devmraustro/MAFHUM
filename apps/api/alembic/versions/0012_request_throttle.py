"""Rate limit the unauthenticated mail-sending endpoints.

Revision ID: 0012_request_throttle
Revises: 0011_account_lifecycle

Why this migration exists
-------------------------
11 introduced password reset and address verification. Both are
*unauthenticated by design* — the caller cannot sign in, which is the entire
reason the endpoints exist — and both send mail. Until now nothing limited how
many times one client could call them, which made password recovery an open
mail-sending endpoint: a flood of reset mail to one address, a burned free-tier
email quota, and recovery down for every real user.

``throttled_actions`` is that limit. It is a separate table from
``login_attempts`` because the two answer different questions and have different
windows; sharing one table would mean a burst of failed logins silently consumed
the recovery budget.

Downgrade drops the table and its indexes. Pending rows are only counters, so
losing them weakens nothing except the current window.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_request_throttle"
down_revision = "0011_account_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "throttled_actions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_throttled_actions_scope_client",
        "throttled_actions",
        ["scope", "client_key", "attempted_at"],
    )
    op.create_index(
        "ix_throttled_actions_attempted",
        "throttled_actions",
        ["attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_throttled_actions_attempted", table_name="throttled_actions")
    op.drop_index("ix_throttled_actions_scope_client", table_name="throttled_actions")
    op.drop_table("throttled_actions")
