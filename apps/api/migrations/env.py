"""Alembic environment for MAFHUM migrations.

The database URL is read from the validated application config
(`app.config.settings.database_url`), so migrations never need a separate
credentials file. When no URL is configured, offline mode is used (no
database connection required).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the app config so DATABASE_URL is resolved from .env / environment.
from app.config import settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # Phase 1: migrations are plain SQL; no ORM metadata yet.


def get_url() -> str:
    url = settings.database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured; cannot run online migrations")
    return url


def run_migrations_offline() -> None:
    """Run migrations without a database connection (SQL output only)."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
