"""Project endpoints. Every query is scoped to the authenticated owner."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession
from mafhum_api.api.schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_response(project: object) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,  # type: ignore[attr-defined]
        title=project.title,  # type: ignore[attr-defined]
        description=project.description,  # type: ignore[attr-defined]
        language=project.language,  # type: ignore[attr-defined]
        created_at=project.created_at,  # type: ignore[attr-defined]
        updated_at=project.updated_at,  # type: ignore[attr-defined]
    )


@router.get("", response_model=ProjectListResponse)
def list_projects(user: CurrentUser, context: Context, db: DbSession) -> ProjectListResponse:
    projects = context.projects.list(db, user.id)
    return ProjectListResponse(items=[_to_response(p) for p in projects], total=len(projects))


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CsrfGuard],
)
def create_project(
    payload: ProjectCreateRequest,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> ProjectResponse:
    project = context.projects.create(
        db,
        owner_id=user.id,
        title=payload.title,
        description=payload.description,
        language=payload.language,
    )
    db.commit()
    return _to_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> ProjectResponse:
    project = context.projects.get_owned(db, user.id, project_id)
    return _to_response(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[CsrfGuard],
)
def delete_project(
    project_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    response: Response,
) -> Response:
    context.projects.delete(db, owner_id=user.id, project_id=project_id)
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
