"""
FlexSearch Backend - Projects API Router

Project CRUD endpoints.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Document, DocumentStatus, Project, User
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    ReindexRequest,
    ReindexResponse,
    project_to_response,
)
from app.schemas.rag_config import RagConfig
from app.services.document_tasks import schedule_process_document
from app.services.document_worker import ReindexMode
from app.services.project_access import (
    has_admin_access,
    user_can_access_project,
    user_owns_project,
)
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/projects", tags=["projects"])


async def _doc_count(db: AsyncSession, project_id: UUID) -> int:
    q = select(func.count()).select_from(Document).where(Document.project_id == project_id)
    return (await db.execute(q)).scalar() or 0


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    rag = (
        project_data.rag_config.to_db()
        if project_data.rag_config
        else RagConfig.from_settings().to_db()
    )
    project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
        rag_config=rag,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    logger.info("Project created: %s by %s", project.name, current_user.email)
    return project_to_response(project, 0)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> ProjectListResponse:
    query = select(Project)
    if not has_admin_access(current_user):
        query = query.where(Project.owner_id == current_user.id)
    query = query.offset(skip).limit(limit).order_by(Project.created_at.desc())
    projects = (await db.execute(query)).scalars().all()

    count_query = select(func.count()).select_from(Project)
    if not has_admin_access(current_user):
        count_query = count_query.where(Project.owner_id == current_user.id)
    total = (await db.execute(count_query)).scalar() or 0

    responses = []
    for project in projects:
        responses.append(
            project_to_response(project, await _doc_count(db, project.id))
        )
    return ProjectListResponse(projects=responses, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_can_access_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project_to_response(project, await _doc_count(db, project.id))


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    if project_data.name is not None:
        project.name = project_data.name
    if project_data.description is not None:
        project.description = project_data.description
    if project_data.rag_config is not None:
        project.rag_config = project_data.rag_config.to_db()

    await db.commit()
    await db.refresh(project)
    return project_to_response(project, await _doc_count(db, project.id))


@router.post("/{project_id}/reindex", response_model=ReindexResponse)
async def reindex_project(
    project_id: UUID,
    body: ReindexRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReindexResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    docs_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.status == DocumentStatus.COMPLETED,
        )
    )
    documents = docs_result.scalars().all()
    mode = ReindexMode(body.mode)
    force = mode == ReindexMode.FULL

    for doc in documents:
        schedule_process_document(
            doc.id,
            project_id,
            force_full_extract=force,
            mode=mode,
        )

    return ReindexResponse(
        processed=len(documents),
        failed=0,
        total_chunks=0,
        message=f"Reindex started for {len(documents)} document(s) in mode={body.mode}",
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    await db.delete(project)
    await db.commit()
