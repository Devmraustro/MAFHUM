"""Project service — every access path enforces server-side ownership."""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SaSession

from app.models.project import Project
from app.models.user import User


class ProjectNotFound(HTTPException):
    """Consistent 404: never reveals whether an ID exists for another user."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )


class ProjectService:
    def __init__(self, db: SaSession, current_user: User) -> None:
        self.db = db
        self.current_user = current_user

    def count_projects(self) -> int:
        return int(
            self.db.execute(
                select(func.count(Project.id)).where(
                    Project.owner_id == self.current_user.id
                )
            ).scalar_one()
        )

    def _enforce_quota(self) -> int:
        actual_count = self.count_projects()
        if actual_count >= self.current_user.project_quota:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project quota reached",
            )
        return actual_count

    def list_projects(self, limit: int, offset: int) -> list[Project]:
        return list(
            self.db.execute(
                select(Project)
                .where(Project.owner_id == self.current_user.id)
                .order_by(Project.created_at.desc(), Project.id.desc())
                .offset(offset)
                .limit(limit)
            ).scalars().all()
        )

    def list_all_owned_projects(self) -> list[Project]:
        return list(
            self.db.execute(
                select(Project)
                .where(Project.owner_id == self.current_user.id)
                .order_by(Project.created_at.asc())
            ).scalars().all()
        )

    def get_project(self, project_id: str) -> Project:
        project = self.db.execute(
            select(Project)
            .where(Project.id == project_id)
            .where(Project.owner_id == self.current_user.id)
        ).scalar_one_or_none()
        if project is None:
            raise ProjectNotFound()
        return project

    def create_project(self, title: str, description: str | None) -> Project:
        actual_count = self._enforce_quota()
        now = datetime.now(UTC)
        project = Project(
            owner_id=self.current_user.id,
            title=title,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self.db.add(project)
        self.current_user.projects_count = actual_count + 1
        self.db.flush()
        return project

    def update_project(
        self,
        project_id: str,
        *,
        title: str | None,
        update_title: bool,
        description: str | None,
        update_description: bool,
    ) -> Project:
        project = self.get_project(project_id)
        if update_title and title is not None:
            project.title = title
        if update_description:
            project.description = description
        project.updated_at = datetime.now(UTC)
        self.db.flush()
        return project

    def delete_project(self, project_id: str) -> None:
        project = self.get_project(project_id)
        self.db.delete(project)
        self.db.flush()
        self.current_user.projects_count = self.count_projects()
        self.db.flush()
