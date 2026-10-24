"""Add a canonical URL identity to source_records for deduplication.

Revision ID: 0003_source_dedup
Revises: 0002_documents
Create Date: 2026-10-23

Notes for operators
-------------------
* Purely additive. The downgrade drops one column and one index and loses no
  source data: the original ``url`` is never modified.
* ``url_normalized`` is derived, not authoritative. It can always be rebuilt from
  ``url`` by re-running the backfill below.
* Uniqueness is a **partial** index on ``(owner_id, url_normalized)`` where the
  value is not NULL. Deduplication is scoped per user on purpose: two students
  cataloguing the same paper hold independent records, and merging across owners
  would leak one user's catalog into another's.
* Rows whose URL cannot be canonicalized stay NULL. NULL means "no identity could
  be established", never "duplicate of another NULL" — the partial index keeps
  those rows from colliding with each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision: str = "0003_source_dedup"
down_revision: str | None = "0002_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _canonicalize(url: str | None) -> str | None:
    """Frozen copy of services.sources.canonical.canonicalize_url.

    Inlined rather than imported: a migration must keep behaving the same way
    years from now, even if the application's normalization rules evolve. If the
    rules change, add a new migration that rebuilds this column.
    """
    if not url:
        return None
    candidate = url.strip()
    if not candidate or len(candidate) > 2048:
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if parts.username or parts.password:
        return None
    port = parts.port
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def upgrade() -> None:
    op.add_column(
        "source_records",
        sa.Column("url_normalized", sa.String(length=1024), nullable=True),
    )

    # Backfill from the preserved original URLs. Done in Python rather than SQL
    # because the rules are not expressible as a single safe expression: a naive
    # ``lower(url)`` would also lowercase the path, and paths are case-sensitive.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, url FROM source_records WHERE url IS NOT NULL")
    ).fetchall()
    for row in rows:
        canonical = _canonicalize(row[1])
        if canonical is not None:
            connection.execute(
                sa.text("UPDATE source_records SET url_normalized = :c WHERE id = :id"),
                {"c": canonical, "id": row[0]},
            )

    op.create_index(
        "uq_sources_owner_url_normalized",
        "source_records",
        ["owner_id", "url_normalized"],
        unique=True,
        postgresql_where=sa.text("url_normalized IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_sources_owner_url_normalized", table_name="source_records")
    op.drop_column("source_records", "url_normalized")
