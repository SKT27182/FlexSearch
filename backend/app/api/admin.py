"""
FlexSearch Backend - Admin API Router

Hierarchy: INFRA_ADMIN (main_db) > ADMIN (FlexSearch-only) > USER
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    get_db,
    is_infra_admin,
    require_admin,
    require_infra_admin,
)
from app.core.security import get_password_hash
from app.db.models import Document, DocumentStatus, Project, RagMode, User, UserRole
from app.schemas.auth import UserResponse
from app.services.project_access import user_can_administer_target
from app.services.project_lifecycle import delete_document_fully, delete_project_fully
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminCreateUser(BaseModel):
    """Schema for admin creating a user."""

    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default="USER", pattern="^(ADMIN|USER)$")


class AdminUpdateUser(BaseModel):
    """Schema for admin updating a user."""

    password: str | None = Field(default=None, min_length=8)


class UserStats(BaseModel):
    """User-wise statistics."""

    user_id: str
    email: str
    role: str
    project_count: int
    document_count: int
    created_at: datetime


class AdminDocumentSummary(BaseModel):
    id: str
    filename: str
    status: str
    size_bytes: int
    chunk_count: int
    created_at: datetime


class AdminProjectSummary(BaseModel):
    id: str
    name: str
    description: str | None
    rag_mode: str
    document_count: int
    created_at: datetime
    documents: list[AdminDocumentSummary]


class AdminUserProjectsResponse(BaseModel):
    user_id: str
    email: str
    role: str
    projects: list[AdminProjectSummary]


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> list[User]:
    """List all users (admin or infra-hub admin)."""
    result = await db.execute(
        select(User).offset(skip).limit(limit).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: AdminCreateUser,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Create a new user. Only infra-hub admins may create ADMIN accounts."""
    if user_data.role == "ADMIN" and not is_infra_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only infra-hub admins can create administrator accounts",
        )

    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        role=UserRole(user_data.role),
        infra_hub_user_id=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"Admin created user: {user.email} with role {user.role}")
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: AdminUpdateUser,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Reset password for a user (admin or infra admin)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if is_infra_admin(user):
        raise HTTPException(
            status_code=403,
            detail="Cannot modify infra-hub admin accounts",
        )
    if body.password:
        user.hashed_password = get_password_hash(body.password)
        await db.commit()
        await db.refresh(user)
    return user


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: UUID,
    role: UserRole,
    _: Annotated[User, Depends(require_infra_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Infra-hub admins may promote/demote between USER and ADMIN only."""
    if role not in (UserRole.USER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only assign USER or ADMIN roles",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if is_infra_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify infra-hub admin accounts",
        )

    user.role = role
    await db.commit()
    await db.refresh(user)

    logger.info(f"User role updated: {user.email} -> {role.value}")
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a user. Infra admins may delete ADMIN/USER; FlexSearch admins only USER."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if is_infra_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete infra-hub admin accounts from FlexSearch",
        )

    if user.role == UserRole.ADMIN and not is_infra_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only infra-hub admins can delete administrator accounts",
        )

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    await db.delete(user)
    await db.commit()

    logger.info(f"User deleted: {user.email}")


@router.get("/users/{user_id}/stats", response_model=UserStats)
async def get_user_stats(
    user_id: UUID,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserStats:
    """Get detailed statistics for a specific user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    project_count = (
        await db.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == user_id)
        )
    ).scalar() or 0

    document_count = (
        await db.execute(
            select(func.count())
            .select_from(Document)
            .join(Project)
            .where(Project.owner_id == user_id)
        )
    ).scalar() or 0

    return UserStats(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        project_count=project_count,
        document_count=document_count,
        created_at=user.created_at,
    )


@router.get("/users/stats/all", response_model=list[UserStats])
async def get_all_user_stats(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 50,
) -> list[UserStats]:
    """Get statistics for all users."""
    result = await db.execute(
        select(User).offset(skip).limit(limit).order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    stats_list = []
    for user in users:
        project_count = (
            await db.execute(
                select(func.count())
                .select_from(Project)
                .where(Project.owner_id == user.id)
            )
        ).scalar() or 0

        document_count = (
            await db.execute(
                select(func.count())
                .select_from(Document)
                .join(Project)
                .where(Project.owner_id == user.id)
            )
        ).scalar() or 0

        stats_list.append(
            UserStats(
                user_id=str(user.id),
                email=user.email,
                role=user.role.value,
                project_count=project_count,
                document_count=document_count,
                created_at=user.created_at,
            )
        )

    return stats_list


async def _require_can_administer_user(
    admin: User,
    target: User,
) -> None:
    if not user_can_administer_target(admin, target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot manage this user's resources",
        )


@router.get("/users/{user_id}/projects", response_model=AdminUserProjectsResponse)
async def list_user_projects(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUserProjectsResponse:
    """List projects and documents for a user (admin hierarchy enforced)."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    await _require_can_administer_user(current_user, target_user)

    projects_result = await db.execute(
        select(Project)
        .where(Project.owner_id == user_id)
        .order_by(Project.created_at.desc())
    )
    projects = projects_result.scalars().all()

    summaries: list[AdminProjectSummary] = []
    for project in projects:
        docs_result = await db.execute(
            select(Document)
            .where(Document.project_id == project.id)
            .order_by(Document.created_at.desc())
        )
        documents = docs_result.scalars().all()
        rag_mode = project.rag_mode
        if isinstance(rag_mode, RagMode):
            rag_mode = rag_mode.value
        summaries.append(
            AdminProjectSummary(
                id=str(project.id),
                name=project.name,
                description=project.description,
                rag_mode=str(rag_mode),
                document_count=len(documents),
                created_at=project.created_at,
                documents=[
                    AdminDocumentSummary(
                        id=str(doc.id),
                        filename=doc.filename,
                        status=doc.status.value
                        if isinstance(doc.status, DocumentStatus)
                        else str(doc.status),
                        size_bytes=doc.file_size,
                        chunk_count=doc.chunk_count,
                        created_at=doc.created_at,
                    )
                    for doc in documents
                ],
            )
        )

    return AdminUserProjectsResponse(
        user_id=str(target_user.id),
        email=target_user.email,
        role=target_user.role.value,
        projects=summaries,
    )


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete another user's project (admin hierarchy enforced)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    owner_result = await db.execute(select(User).where(User.id == project.owner_id))
    owner = owner_result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail="Project owner not found")
    await _require_can_administer_user(current_user, owner)

    await delete_project_fully(db, project)
    await db.commit()
    logger.info(
        "Admin %s deleted project %s owned by %s",
        current_user.email,
        project_id,
        owner.email,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_document(
    document_id: UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete any document (admin only, bypasses ownership)."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    owner_result = await db.execute(
        select(User)
        .join(Project, Project.owner_id == User.id)
        .where(Project.id == document.project_id)
    )
    owner = owner_result.scalar_one_or_none()
    if owner:
        await _require_can_administer_user(current_user, owner)

    await delete_document_fully(db, document, project_id=document.project_id)
    await db.commit()

    logger.info(f"Admin deleted document: {document_id}")


@router.get("/documents")
async def list_all_documents(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
    status_filter: str | None = None,
) -> list[dict]:
    """List all documents across all projects (admin only)."""
    query = (
        select(
            Document,
            Project.name.label("project_name"),
            User.email.label("owner_email"),
        )
        .join(Project, Document.project_id == Project.id)
        .join(User, Project.owner_id == User.id)
    )

    if status_filter:
        try:
            status_enum = DocumentStatus(status_filter)
            query = query.where(Document.status == status_enum)
        except ValueError:
            pass

    query = query.offset(skip).limit(limit).order_by(Document.created_at.desc())
    result = await db.execute(query)

    documents = []
    for doc, project_name, owner_email in result.all():
        documents.append(
            {
                "id": str(doc.id),
                "filename": doc.filename,
                "content_type": doc.content_type,
                "size_bytes": doc.file_size,
                "status": doc.status.value,
                "chunk_count": doc.chunk_count,
                "project_id": str(doc.project_id),
                "project_name": project_name,
                "owner_email": owner_email,
                "created_at": doc.created_at.isoformat(),
            }
        )

    return documents


@router.get("/stats")
async def get_system_stats(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get system statistics (admin only)."""
    user_count = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar() or 0

    infra_admin_count = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.INFRA_ADMIN)
        )
    ).scalar() or 0

    admin_count = (
        await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
        )
    ).scalar() or 0

    regular_count = (
        await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.USER)
        )
    ).scalar() or 0

    project_count = (
        await db.execute(select(func.count()).select_from(Project))
    ).scalar() or 0

    document_count = (
        await db.execute(select(func.count()).select_from(Document))
    ).scalar() or 0

    status_counts = await db.execute(
        select(Document.status, func.count(Document.id)).group_by(Document.status)
    )
    doc_status = {status.value: count for status, count in status_counts.all()}

    return {
        "users": {
            "total": user_count,
            "infra_admins": infra_admin_count,
            "admins": admin_count,
            "regular": regular_count,
        },
        "projects": project_count,
        "documents": {
            "total": document_count,
            "by_status": doc_status,
        },
    }
