"""
FlexSearch Backend - Projects API Router

Project CRUD endpoints.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Document, Project, User
from app.services.project_access import has_admin_access, user_can_access_project, user_owns_project
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Project:
    """Create a new project."""
    project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
    )

    db.add(project)
    await db.commit()
    await db.refresh(project)

    logger.info(f"Project created: {project.name} by {current_user.email}")

    return project


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> ProjectListResponse:
    """List projects. Admins see all projects; regular users see their own."""
    query = select(Project)
    if not has_admin_access(current_user):
        query = query.where(Project.owner_id == current_user.id)
    query = query.offset(skip).limit(limit).order_by(Project.created_at.desc())

    result = await db.execute(query)
    projects = result.scalars().all()

    count_query = select(func.count()).select_from(Project)
    if not has_admin_access(current_user):
        count_query = count_query.where(Project.owner_id == current_user.id)
    total = (await db.execute(count_query)).scalar() or 0

    # Get document counts for each project
    project_responses = []
    for project in projects:
        doc_count_query = (
            select(func.count())
            .select_from(Document)
            .where(Document.project_id == project.id)
        )
        doc_count = (await db.execute(doc_count_query)).scalar() or 0

        project_responses.append(
            ProjectResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                owner_id=project.owner_id,
                created_at=project.created_at,
                updated_at=project.updated_at,
                document_count=doc_count,
            )
        )

    return ProjectListResponse(projects=project_responses, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    """Get a specific project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if not user_can_access_project(current_user, project):
        logger.warning(
            "Project access denied: project=%s user=%s",
            project_id,
            current_user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this project",
        )

    # Get document count
    doc_count_query = (
        select(func.count())
        .select_from(Document)
        .where(Document.project_id == project.id)
    )
    doc_count = (await db.execute(doc_count_query)).scalar() or 0

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        document_count=doc_count,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Project:
    """Update a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if not user_owns_project(current_user, project):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this project",
        )

    # Update fields
    if project_data.name is not None:
        project.name = project_data.name
    if project_data.description is not None:
        project.description = project_data.description

    await db.commit()
    await db.refresh(project)

    logger.info(f"Project updated: {project.name}")

    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a project and all associated data."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if not user_owns_project(current_user, project):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this project",
        )

    await db.delete(project)
    await db.commit()

    logger.info(f"Project deleted: {project.name}")
