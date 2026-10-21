"""Project routes — every operation enforces server-side ownership."""

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session as SaSession

from app.api.deps import require_csrf, require_db, require_user
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate
from app.services.project import ProjectService

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    request: Request,
    payload: ProjectCreate,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> ProjectOut:
    require_csrf(request)
    service = ProjectService(db, user)
    project = service.create_project(payload.title, payload.description)
    db.commit()
    db.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("/projects", response_model=ProjectList)
def list_projects(
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProjectList:
    service = ProjectService(db, user)
    items = service.list_projects(limit, offset)
    total = service.count_projects()
    return ProjectList(
        items=[ProjectOut.model_validate(i) for i in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> ProjectOut:
    service = ProjectService(db, user)
    project = service.get_project(project_id)
    return ProjectOut.model_validate(project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    request: Request,
    project_id: str,
    payload: ProjectUpdate,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> ProjectOut:
    require_csrf(request)
    service = ProjectService(db, user)
    project = service.update_project(
        project_id,
        title=payload.title,
        update_title=("title" in payload.model_fields_set),
        description=payload.description,
        update_description=("description" in payload.model_fields_set),
    )
    db.commit()
    db.refresh(project)
    return ProjectOut.model_validate(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    request: Request,
    project_id: str,
    user: User = Depends(require_user),
    db: SaSession = Depends(require_db),
) -> None:
    require_csrf(request)
    service = ProjectService(db, user)
    service.delete_project(project_id)
    db.commit()
