"""Alembic environment.

The URL comes from ``MAFHUM_DATABASE_URL`` (explicit override), then
``DATABASE_URL`` (the same variable the application reads), and finally
``alembic.ini``. Migrations always target the real SQLAlchemy metadata so an
autogenerate diff cannot silently drift from the models.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mafhum_api.db import models  # noqa: F401  (registers tables on the metadata)
from mafhum_api.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The application (pydantic-settings, no env_prefix) and `.env.example` both use
# the unprefixed `DATABASE_URL`. Migrations must read the *same* variable, or an
# operator can migrate one database while the application serves another.
# `MAFHUM_DATABASE_URL` is still honoured as an explicit override for the
# common case of migrating a different target than the running app.
_env_url = os.environ.get("MAFHUM_DATABASE_URL") or os.environ.get("DATABASE_URL")
if _env_url:
    # Alembic uses the plain psycopg2/psycopg URL scheme, not SQLAlchemy's.
    _url = _env_url.replace("+psycopg", "")
    # `set_main_option` stores the value in a ConfigParser section, which treats
    # `%` as interpolation syntax. A legitimate URL can contain percent-encoded
    # characters (a password, or a unix-socket directory in `host=`), so escape
    # them or ConfigParser raises ValueError / mangles the URL.
    config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
