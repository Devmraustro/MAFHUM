"""SQLAlchemy engine/session wiring."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.sql.expression import Executable


class Base(DeclarativeBase):
    """Declarative base shared by every MAFHUM model."""


class Database:
    """Owns an engine and produces sessions.

    Kept as an explicit object (not module-level globals) so tests can point it
    at a disposable PostgreSQL instance.
    """

    def __init__(self, url: str, *, pool_size: int = 5, max_overflow: int = 10) -> None:
        self._url = url
        kwargs: dict[str, object] = {"future": True}
        if url.startswith("postgresql"):
            kwargs.update({"pool_size": pool_size, "max_overflow": max_overflow})
        self._engine: Engine = create_engine(url, **kwargs)
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    @property
    def supports_row_locking(self) -> bool:
        """True when ``SELECT ... FOR UPDATE SKIP LOCKED`` is available."""
        return self._engine.dialect.name == "postgresql"

    def session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def unit_of_work(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self._engine.dispose()


def execute_rowcount(session: Session, statement: Executable) -> int:
    """Execute a DML statement and return how many rows it affected.

    ``Session.execute`` is typed as returning ``Result``, but DML statements
    return a ``CursorResult`` at runtime; the cast keeps the rowcount access
    type-safe without sprinkling ignores across call sites.
    """
    result = session.execute(statement)
    return int(cast("CursorResult[Any]", result).rowcount or 0)
