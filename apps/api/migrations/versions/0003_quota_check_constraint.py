"""Enforce DB-level quota upper bound on users.projects_count (Phase 2 hardening).

Reconciles `users.projects_count` with actual owned `projects` rows without
deleting user data, and adds `ck_users_projects_within_quota`
(`projects_count <= project_quota`) cleanly on both PostgreSQL and SQLite.

Note: revision ID is kept strictly <= 32 characters (`0003_quota_check_constraint`,
27 chars) because Alembic's `alembic_version.version_num` column is `VARCHAR(32)`
on PostgreSQL (whereas SQLite does not enforce `VARCHAR(N)` lengths).
"""

revision = "0003_quota_check_constraint"
down_revision = "0002_identity_and_projects"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402,I001  (Alembic requires revision ids before imports)
import sqlalchemy as sa  # noqa: E402,I001  (Alembic requires revision ids before imports)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE users SET projects_count = ("
            "SELECT COUNT(*) FROM projects WHERE projects.owner_id = users.id"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE users SET project_quota = projects_count "
            "WHERE projects_count > project_quota"
        )
    )

    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            with op.batch_alter_table("users") as batch_op:
                batch_op.create_check_constraint(
                    "ck_users_projects_within_quota",
                    "projects_count <= project_quota",
                )
        finally:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
    else:
        op.create_check_constraint(
            "ck_users_projects_within_quota",
            "users",
            "projects_count <= project_quota",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            with op.batch_alter_table("users") as batch_op:
                batch_op.drop_constraint(
                    "ck_users_projects_within_quota", type_="check"
                )
        finally:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
    else:
        op.drop_constraint(
            "ck_users_projects_within_quota", "users", type_="check"
        )
