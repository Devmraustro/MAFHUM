"""Record the malware-scan verdict on each document.

Revision ID: 0007_document_scan_verdict
Revises: 0006_login_throttle
Create Date: 2026-10-23

Why
---
Without a stored verdict there is no way to distinguish a document an engine
actually examined from one that was accepted because no engine was configured.
Both looked identical, which is how an unscanned file ends up looking vouched
for. The column is nullable on purpose: NULL means "never asked", not "clean".

Notes for operators
-------------------
* Purely additive and nullable, so it needs no backfill and takes no write lock
  beyond adding the column. Existing rows stay NULL, correctly reporting that
  they were never scanned.
* The downgrade drops the column and therefore the audit trail of which uploads
  were scanned. No document content is affected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_document_scan_verdict"
down_revision: str | None = "0006_login_throttle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("scan_verdict", sa.String(length=24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "scan_verdict")
