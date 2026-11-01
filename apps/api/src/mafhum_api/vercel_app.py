"""Vercel serverless deployment entrypoint.

Phase 1 of the Vercel-native migration (see
``docs/VERCEL_NATIVE_MIGRATION_PLAN.md``): the smallest supported way to serve
the existing FastAPI application on the Vercel Python runtime, without
changing the container deployment path, the web project, or any security
control.

What this module changes relative to ``mafhum_api.main:app`` — and nothing
else:

1. **No in-process worker.** The application is built with
   ``start_worker=False``: document processing threads are never started at
   cold start, even if ``PROCESSING_WORKER_ENABLED`` is mistakenly left on.
   Durable processing is Phase 3 of the migration plan; until then a
   serverless deployment honestly reports ``processing_worker: stopped``.
2. **PostgreSQL-only, fail-fast.** Vercel does not change the database story:
   a non-PostgreSQL ``DATABASE_URL`` aborts the cold start instead of letting
   the app run against an untested (or silently invented) backend. SQLite is
   never a fallback.
3. **Bounded connection lifecycle.** The pool keeps the operator-supplied
   ``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW`` bounds and can enable a pre-ping
   (``DB_POOL_PRE_PING``) so connections that went stale between invocations
   are replaced instead of serving errors. The lifespan disposes the engine
   on shutdown exactly as the container deployment does.

Migrations are NOT run here — not at import time and not in the lifespan.
Schema changes remain a controlled release step (the container entrypoint or
an explicit ``alembic upgrade`` against the target database), so a function
cold start can never mutate the schema.

The module-level ``app`` is the variable the Vercel Python runtime loads (via
``api/index.py`` and ``tool.vercel.entrypoint`` in ``pyproject.toml``). It is
built once per cold start; the FastAPI lifespan runs when Vercel starts the
function instance and its teardown (limited to 500 ms after SIGTERM on
Vercel) only has to dispose the connection pool.
"""

from __future__ import annotations

from fastapi import FastAPI

from mafhum_api.config import Settings, get_settings
from mafhum_api.main import create_app

#: The only supported production database schemes. Deliberately explicit so
#: that a misconfigured serverless deployment fails at cold start with an
#: actionable message instead of falling back to some other backend.
#: ``postgresql://`` resolves to the same psycopg driver in this project
#: (psycopg 3 is the only installed PostgreSQL driver).
_SUPPORTED_DATABASE_PREFIXES = ("postgresql://", "postgresql+psycopg://")


def assert_postgres_only(settings: Settings) -> None:
    """Refuse to serve on the serverless path without PostgreSQL.

    This is a deployment-path guard: the ``Database`` layer keeps its
    library-level behaviour (tests may exercise other dialects), but the
    Vercel application never boots against anything except PostgreSQL.
    """
    if not settings.database_url.startswith(_SUPPORTED_DATABASE_PREFIXES):
        raise RuntimeError(
            "Vercel deployment requires a PostgreSQL DATABASE_URL (postgresql:// "
            "or postgresql+psycopg://); refusing to start with "
            f"{settings.database_url.split(':', 1)[0] or '(empty)'!r}. SQLite "
            "is not a production fallback."
        )


def create_vercel_app(settings: Settings | None = None) -> FastAPI:
    """Build the application for serverless execution.

    ``start_worker=False`` is a code-level guarantee, independent of any
    environment variable: serverless invocations must not rely on, or start,
    in-process background threads.
    """
    settings = settings or get_settings()
    assert_postgres_only(settings)
    application = create_app(settings, start_worker=False)
    application.state.serverless_deployment = True
    return application


app = create_vercel_app()

__all__ = ["app", "assert_postgres_only", "create_vercel_app"]
