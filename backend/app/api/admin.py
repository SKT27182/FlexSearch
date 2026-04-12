"""
FlexSearch Backend - Admin API Router

Admin-only endpoints for user management, file management, and system overview.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin
from app.core.security import get_password_hash
from app.db.models import Document, DocumentStatus, Project, User, UserRole
from app.schemas.auth import UserResponse
from app.services.storage import get_storage_service
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ============ Schemas ============


class AdminCreateUser(BaseModel):
    """Schema for admin creating a user."""

    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default="USER", pattern="^(ADMIN|USER)$")


class UserStats(BaseModel):
    """User-wise statistics."""

    user_id: str
    email: str
    role: str
    project_count: int
    document_count: int
    created_at: datetime


# ============ User Management ============


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> list[User]:
    """List all users (admin only)."""
    result = await db.execute(
        select(User).offset(skip).limit(limit).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: AdminCreateUser,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Create a new user with specified role (admin only)."""
    # Check if email already exists
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
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"Admin created user: {user.email} with role {user.role}")
    return user


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: UUID,
    role: str,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Update a user's role (admin only)."""
    if role not in ["ADMIN", "USER"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be ADMIN or USER",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-demotion
    if user.id == admin.id and role == "USER":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote yourself",
        )

    user.role = UserRole(role)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User role updated: {user.email} -> {role}")
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a user (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-deletion
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    await db.delete(user)
    await db.commit()

    logger.info(f"User deleted: {user.email}")


# ============ User-wise Statistics ============


@router.get("/users/{user_id}/stats", response_model=UserStats)
async def get_user_stats(
    user_id: UUID,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserStats:
    """Get detailed statistics for a specific user."""
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get project count
    project_count = (
        await db.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == user_id)
        )
    ).scalar() or 0

    # Get document count
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
        # Project count
        project_count = (
            await db.execute(
                select(func.count())
                .select_from(Project)
                .where(Project.owner_id == user.id)
            )
        ).scalar() or 0

        # Document count
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


# ============ File Management ============


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_document(
    document_id: UUID,
    _: Annotated[User, Depends(require_admin)],
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

    # Delete from storage
    try:
        storage = get_storage_service()
        if document.storage_path:
            storage.delete_file(document.storage_path)
    except Exception as e:
        logger.warning(f"Failed to delete file from storage: {e}")

    # Delete from vector store
    try:
        vector_store = get_vector_store()
        vector_store.delete_by_document(str(document_id))
    except Exception as e:
        logger.warning(f"Failed to delete vectors: {e}")

    # Delete from database
    await db.delete(document)
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


# ============ System Statistics ============


@router.get("/stats")
async def get_system_stats(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get system statistics (admin only)."""
    user_count = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar() or 0

    admin_count = (
        await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
        )
    ).scalar() or 0

    project_count = (
        await db.execute(select(func.count()).select_from(Project))
    ).scalar() or 0

    document_count = (
        await db.execute(select(func.count()).select_from(Document))
    ).scalar() or 0

    # Document status breakdown
    status_counts = await db.execute(
        select(Document.status, func.count(Document.id)).group_by(Document.status)
    )
    doc_status = {status.value: count for status, count in status_counts.all()}

    return {
        "users": {
            "total": user_count,
            "admins": admin_count,
            "regular": user_count - admin_count,
        },
        "projects": project_count,
        "documents": {
            "total": document_count,
            "by_status": doc_status,
        },
    }
