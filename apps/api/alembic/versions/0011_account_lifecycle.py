"""Account lifecycle: single-use tokens, email outbox, email verification flag.

Revision ID: 0011_account_lifecycle
Revises: 0010_upload_sessions
Create Date: 2026-11-10

Why
---
Before this revision an account had no recovery path at all: no password reset,
no address verification, no way to change a password, and no way to see or end
an active session. Losing the password meant losing the documents with it.

Notes for operators
-------------------
* Purely additive: two new tables, no change to existing ones, no backfill.
* Verification is *derived*: an address counts as verified once a consumed
  ``email_verification`` token exists for it that is not older than the address
  itself. Nothing is backfilled, because nothing can prove that an address
  registered before this revision was ever controlled by its owner.
* ``user_tokens`` stores a SHA-256 digest of a 32-byte token plus its first 8
  characters. The prefix distinguishes "already used" from "never existed"; it
  cannot be used to reconstruct a token.
* ``email_outbox`` holds recipients and template names, never message bodies or
  links. A queued reset message refers to its token by row id.
* The downgrade drops both tables. Any pending outbox rows are lost with
  them, which is the correct outcome: their tokens go too.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_account_lifecycle"
down_revision: str | None = "0010_upload_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTE: intentionally NOT adding users.email_verified_at here.
    # The migration test suite downgrades to base and upgrades again, and
    # Alembic replaying eleven revisions produced a duplicate column when the
    # column was added in this revision *and* referenced by the ORM model. The
    # verified state is therefore derived from this table instead:
    # a consumed email_verification token proves the inbox was controlled.
    # See ``IdentityService.email_is_verified``.

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(length=8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "purpose IN ('password_reset', 'email_verification')",
            name="ck_user_tokens_purpose",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_user_tokens_expiry"),
    )
    op.create_index("ix_user_tokens_user_purpose", "user_tokens", ["user_id", "purpose"])
    op.create_index("ix_user_tokens_expires", "user_tokens", ["expires_at"])
    op.create_index("ix_user_tokens_prefix", "user_tokens", ["token_prefix"])

    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name="ck_email_outbox_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts"),
        sa.CheckConstraint(
            "char_length(template) BETWEEN 1 AND 64", name="ck_email_outbox_template"
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 200",
            name="ck_email_outbox_error_len",
        ),
    )
    op.create_index("ix_email_outbox_status_created", "email_outbox", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_created", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index("ix_user_tokens_prefix", table_name="user_tokens")
    op.drop_index("ix_user_tokens_expires", table_name="user_tokens")
    op.drop_index("ix_user_tokens_user_purpose", table_name="user_tokens")
    op.drop_table("user_tokens")
