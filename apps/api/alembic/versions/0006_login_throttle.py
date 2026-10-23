"""Database-backed login throttle.

Revision ID: 0006_login_throttle
Revises: 0005_saved_research
Create Date: 2026-10-23

Why this table exists
---------------------
The login throttle lived in a module-level dictionary. That made the configured
attempt limit per *process*, so N replicas allowed roughly N times the intended
volume -- the limit stopped meaning what it said. Counting in the database makes
it true regardless of how many API processes serve traffic.

Notes for operators
-------------------
* Purely additive; the downgrade drops one table and loses throttle history only.
  No user data is affected.
* Rows are pruned when a new attempt is recorded, so the table stays bounded. A
  deployment that stops receiving logins keeps its last window's rows until the
  next attempt; a periodic ``DELETE FROM login_attempts WHERE attempted_at <
  now() - interval '1 hour'`` is safe if you prefer an explicit sweep.
* ``client_key`` is a hashed identifier, not a raw IP address. The throttle only
  compares keys for equality, so the plaintext is never stored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_login_throttle"
down_revision: str | None = "0005_saved_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_attempts_key_time", "login_attempts", ["client_key", "attempted_at"]
    )
    op.create_index("ix_login_attempts_time", "login_attempts", ["attempted_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempts_time", table_name="login_attempts")
    op.drop_index("ix_login_attempts_key_time", table_name="login_attempts")
    op.drop_table("login_attempts")
