"""Dependencies — database engine created lazily from validated config."""


from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import settings

_engine: Engine | None = None


def get_engine() -> Engine | None:
    """Return the configured engine, or None when no database is configured.

    Local development and tests run without Postgres until Docker Compose is
    up; endpoints degrade gracefully instead of crashing.
    """
    global _engine
    if _engine is None and settings.database_url:
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine
