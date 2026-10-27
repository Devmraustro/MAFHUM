"""Project CRUD with mandatory ownership scoping."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from mafhum_api.core.errors import ConflictError, NotFoundError, ValidationError
from mafhum_api.db.models import Project

logger = logging.getLogger(__name__)

TITLE_MAX = 200
DESCRIPTION_MAX = 4000
_MAX_PROJECTS_PER_USER = 100


class ProjectService:
    def list(self, db: Session, owner_id: uuid.UUID) -> list[Project]:
        """Return only the caller's projects, newest first."""
        rows = db.scalars(
            select(Project).where(Project.owner_id == owner_id).order_by(Project.created_at.desc())
        ).all()
        return list(rows)

    def get_owned(self, db: Session, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        """Fetch a project *and* verify ownership in one query.

        Ownership is part of the WHERE clause, so a wrong owner is
        indistinguishable from a missing project — probing an identifier cannot
        confirm that someone else's project exists.
        """
        project = db.scalar(
            select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        )
        if project is None:
            raise NotFoundError("المشروع غير موجود.")
        return project

    def assert_owned(
        self, db: Session, owner_id: uuid.UUID, project_id: uuid.UUID | None
    ) -> Project | None:
        """Validate an optional project reference during upload."""
        if project_id is None:
            return None
        return self.get_owned(db, owner_id, project_id)

    def create(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        title: str,
        description: str = "",
        language: str = "ar",
    ) -> Project:
        title = title.strip()
        if not title:
            raise ValidationError("عنوان المشروع مطلوب.", field_errors={"title": ["مطلوب."]})
        if len(title) > TITLE_MAX:
            raise ValidationError("عنوان المشروع طويل جدًا.", field_errors={"title": ["طويل جدًا."]})
        count = len(self.list(db, owner_id))
        if count >= _MAX_PROJECTS_PER_USER:
            raise ConflictError("وصلت إلى الحد الأقصى من المشاريع.")
        project = Project(
            owner_id=owner_id,
            title=title,
            description=description.strip()[:DESCRIPTION_MAX],
            language=language or "ar",
        )
        db.add(project)
        db.flush()
        logger.info("project created", extra={"event": "project_created", "user_id": str(owner_id)})
        return project

    def delete(self, db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = self.get_owned(db, owner_id, project_id)
        # Documents survive project deletion; their FK is SET NULL by the schema.
        db.delete(project)
        db.flush()
        logger.info("project deleted", extra={"event": "project_deleted"})
