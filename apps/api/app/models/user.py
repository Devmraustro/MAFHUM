"""User account model."""

import re
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Id, UpdatedAt

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.session import Session

EMAIL_RE = re.compile(r"^[^@\s\x00-\x1f]+@[^@\s\x00-\x1f]+\.[^@\s\x00-\x1f]{2,}\Z")


class User(Base):
    __tablename__ = "users"

    id: Mapped[Id]
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preferred_locale: Mapped[str] = mapped_column(String(8), default="ar", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    project_quota: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    projects_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email_lower"),
        CheckConstraint("project_quota >= 0", name="ck_users_project_quota_nonneg"),
        CheckConstraint("projects_count >= 0", name="ck_users_projects_count_nonneg"),
        CheckConstraint("preferred_locale IN ('ar', 'fr')", name="ck_users_preferred_locale"),
    )
