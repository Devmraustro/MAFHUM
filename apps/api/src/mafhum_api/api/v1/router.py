"""Aggregate ``/api/v1`` router."""

from __future__ import annotations

from fastapi import APIRouter

from mafhum_api.api.v1 import (
    account,
    admin,
    auth,
    documents,
    presentations,
    projects,
    research,
    sources,
    study,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(documents.router)
api_router.include_router(sources.router)
api_router.include_router(research.router)
api_router.include_router(study.router)
api_router.include_router(account.router)
api_router.include_router(admin.router)
api_router.include_router(presentations.router)
api_router.include_router(presentations.artifact_router)

__all__ = ["api_router"]
