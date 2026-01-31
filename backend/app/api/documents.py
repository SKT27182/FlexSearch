"""
FlexSearch Backend - Documents API Router

Document upload and management endpoints.
"""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Document, DocumentStatus, Project, User
from app.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


async def verify_project_access(
    project_id: UUID,
    current_user: User,
    db: AsyncSession,
) -> Project:
    """Verify user has access to the project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this project",
        )

    return project


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    project_id: UUID,
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentResponse:
    """
    Upload a document to a project.

    Supported formats: PDF, TXT, MD, images (PNG, JPG, JPEG).
    """
    await verify_project_access(project_id, current_user, db)

    # Validate file type
    allowed_types = {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }

    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type {file.content_type} not supported",
        )

    # Read file content
    content = await file.read()
    file_size = len(content)

    # Generate storage path
    storage_path = f"{project_id}/{file.filename}"

    # Create document record
    document = Document(
        project_id=project_id,
        filename=file.filename or "untitled",
        content_type=file.content_type or "application/octet-stream",
        storage_path=storage_path,
        file_size=file_size,
        status=DocumentStatus.PENDING,
    )

    db.add(document)
    await db.commit()
    await db.refresh(document)

    try:
        # Upload to MinIO
        from app.services.storage import get_storage_service

        storage = get_storage_service()
        storage.upload_file(
            path=storage_path,
            data=content,
            content_type=file.content_type or "application/octet-stream",
        )

        # Trigger RAG ingestion pipeline
        from app.rag.pipeline import get_rag_pipeline

        rag_pipeline = get_rag_pipeline()
        chunk_count = await rag_pipeline.ingest_document(
            content=content,
            content_type=file.content_type or "application/octet-stream",
            filename=file.filename or "untitled",
            document_id=str(document.id),
            project_id=str(project_id),
        )

        # Update document status to COMPLETED
        document.status = DocumentStatus.COMPLETED
        document.chunk_count = chunk_count
        document.processed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(document)

        logger.info(
            f"Document processed: {file.filename} ({chunk_count} chunks) "
            f"to project {project_id}"
        )

    except Exception as e:
        # Update status to FAILED on error
        logger.error(f"Failed to process document {file.filename}: {e}")
        document.status = DocumentStatus.FAILED
        document.error_message = str(e)
        await db.commit()
        await db.refresh(document)
        # Don't raise - return document with FAILED status

    return document


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> DocumentListResponse:
    """List all documents in a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Document)
        .where(Document.project_id == project_id)
        .offset(skip)
        .limit(limit)
        .order_by(Document.created_at.desc())
    )

    result = await db.execute(query)
    documents = result.scalars().all()

    count_query = (
        select(func.count())
        .select_from(Document)
        .where(Document.project_id == project_id)
    )
    total = (await db.execute(count_query)).scalar() or 0

    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(doc) for doc in documents],
        total=total,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Document:
    """Get a specific document."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a document."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Delete from MinIO
    try:
        from app.services.storage import get_storage_service

        storage = get_storage_service()
        if storage.file_exists(document.storage_path):
            storage.delete_file(document.storage_path)
            logger.info(f"Deleted file from MinIO: {document.storage_path}")
    except Exception as e:
        logger.error(f"Failed to delete file from MinIO: {e}")
        # Continue with deletion even if MinIO fails

    # Delete from Qdrant
    try:
        from app.rag.pipeline import get_rag_pipeline

        rag_pipeline = get_rag_pipeline()
        rag_pipeline.delete_document_data(str(document.id))
        logger.info(f"Deleted vectors from Qdrant for document: {document.id}")
    except Exception as e:
        logger.error(f"Failed to delete vectors from Qdrant: {e}")
        # Continue with deletion even if Qdrant fails

    # Delete from database
    await db.delete(document)
    await db.commit()

    logger.info(f"Document deleted: {document.filename}")
