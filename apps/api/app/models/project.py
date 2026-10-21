"""Project model (user-owned academic work container)."""

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Id, UpdatedAt

if TYPE_CHECKING:
    from app.models.user import User


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[Id]
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    owner: Mapped["User"] = relationship(back_populates="projects")

    __table_args__ = (
        CheckConstraint("length(trim(title)) >= 1", name="ck_projects_title_nonempty"),
        CheckConstraint("length(title) <= 200", name="ck_projects_title_maxlen"),
    )
