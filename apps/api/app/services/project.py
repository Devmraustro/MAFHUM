"""Project service — server-side ownership & concurrency-safe quota enforcement.

Concurrency design:
- On PostgreSQL (`READ COMMITTED`), concurrent `create_project` / `delete_project`
  calls for the same user acquire an exclusive row lock on the parent `users`
  row (`SELECT ... FROM users WHERE id = :uid FOR UPDATE`) *before* counting or
  mutating rows in `projects`. Because the parent `users` row always exists even
  when the user owns 0 projects, locking the parent row prevents the classic
  empty-result TOCTOU race without blocking other users.
- In addition, `users.projects_count` is maintained inside the same atomic
  transaction and bounded by SQL constraint `ck_users_projects_within_quota`
  (`projects_count <= project_quota`), rolling back cleanly if ever violated.
- On SQLite (local dev/tests), write serialization acquires an upfront SQLite
  write lock (`UPDATE users SET projects_count = projects_count WHERE id = :uid`
  with `busy_timeout`). Note: SQLite uses database-level single-writer locking
  and is not equivalent to PostgreSQL multi-process MVCC concurrency.
"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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

    def _lock_user_row_for_mutation(self) -> User:
        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        if dialect == "postgresql":
            stmt = (
                select(User)
                .where(User.id == self.current_user.id)
                .with_for_update()
            )
            locked = self.db.execute(stmt).scalar_one_or_none()
        elif dialect == "sqlite":
            # Acquire SQLite RESERVED write lock immediately as the first write
            # statement of the transaction so concurrent local threads serialize
            # cleanly before reading COUNT(*).
            self.db.execute(
                text("UPDATE users SET projects_count = projects_count WHERE id = :uid"),
                {"uid": self.current_user.id},
            )
            locked = self.db.execute(
                select(User).where(User.id == self.current_user.id)
            ).scalar_one_or_none()
        else:
            locked = self.db.execute(
                select(User).where(User.id == self.current_user.id)
            ).scalar_one_or_none()

        if locked is None or not locked.is_active:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        return locked

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
        locked_user = self._lock_user_row_for_mutation()
        actual_count = self.count_projects()
        if actual_count >= locked_user.project_quota:
            locked_user.projects_count = actual_count
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project quota reached",
            )

        now = datetime.now(UTC)
        project = Project(
            owner_id=locked_user.id,
            title=title,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self.db.add(project)
        locked_user.projects_count = actual_count + 1
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project quota reached",
            ) from None
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
        locked_user = self._lock_user_row_for_mutation()
        project = self.get_project(project_id)
        self.db.delete(project)
        self.db.flush()
        locked_user.projects_count = self.count_projects()
        self.db.flush()
