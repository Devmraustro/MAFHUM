"""Initial MAFHUM schema seed.

Phase 1 creates only the system_settings table (key/value store for runtime
configuration and migration bookkeeping). Domain tables (users, projects,
documents, sources, claims, ...) are added in later phases, one migration per
phase, each reviewed and tested before merge.
"""

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402,I001  (Alembic requires revision ids before imports)
from alembic import op  # noqa: E402,I001  (Alembic requires revision ids before imports)


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.execute(
        "INSERT INTO system_settings (key, value, description) VALUES "
        "('app_version', '0.1.0', 'MAFHUM API version'), "
        "('migration_baseline', 'phase_1_skeleton', 'Baseline created in Phase 1')"
    )


def downgrade() -> None:
    op.drop_table("system_settings")
