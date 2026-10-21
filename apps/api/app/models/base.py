"""Shared SQLAlchemy declarative base and column helpers."""

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Identifiers are UUID values generated in Python and stored as VARCHAR so the
# same schema works on PostgreSQL (deployment) and SQLite (local tests).
Id = Annotated[
    str,
    mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4())),
]
FkId = Annotated[str, mapped_column(String(255), nullable=False)]
CreatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
]
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
]


class Base(DeclarativeBase):
    pass
