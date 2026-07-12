"""
FlexSearch Backend - Documents API Router

Document upload and management endpoints.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_sse import stream_document_events, stream_project_events
from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Document, DocumentStatus, Project, User
from app.schemas.document import (
    DocumentContentResponse,
    DocumentListResponse,
    DocumentResponse,
)
from app.services.document_status import update_document_status
from app.services.document_storage import (
    extracted_md_key,
    extracted_meta_key,
    raw_object_key,
)
from app.services.document_tasks import (
    cancel_document_ingest,
    schedule_process_document,
)
from app.services.project_access import user_can_access_project
from app.services.storage import get_storage_service
from app.rag.pipeline import create_pipeline
from app.services.document_worker import get_project_rag_context
from app.services.summary_tasks import cancel_document_summary
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])

PREVIEW_STATUSES = {
    DocumentStatus.EXTRACTED,
    DocumentStatus.CHUNKING,
    DocumentStatus.INDEXING,
    DocumentStatus.COMPLETED,
}

CONTENT_MAX_CHARS = 500_000


async def verify_project_access(
    project_id: UUID,
    current_user: User,
    db: AsyncSession,
) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if not user_can_access_project(current_user, project):
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
    """Upload a document; processing runs in the background."""
    await verify_project_access(project_id, current_user, db)

    allowed_types = {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }

    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type {file.content_type} not supported",
        )

    content = await file.read()
    file_size = len(content)
    filename = file.filename or "untitled"

    document = Document(
        project_id=project_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        storage_path="",
        file_size=file_size,
        status=DocumentStatus.UPLOADED,
        processing_step="Upload received",
        progress_pct=10,
    )
    db.add(document)
    await db.flush()

    storage_path = raw_object_key(project_id, document.id, filename)
    document.storage_path = storage_path
    await db.commit()
    await db.refresh(document)

    storage = get_storage_service()
    storage.upload_file(
        path=storage_path,
        data=content,
        content_type=document.content_type,
    )

    await update_document_status(
        db,
        document,
        status=DocumentStatus.STORED,
        processing_step="Saved to storage",
        progress_pct=25,
    )

    schedule_process_document(document.id, project_id)

    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> DocumentListResponse:
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


@router.get("/events")
async def project_document_events(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    await verify_project_access(project_id, current_user, db)

    async def event_generator():
        async for chunk in stream_project_events(db, project_id):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentResponse:
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

    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/events")
async def document_events(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    await verify_project_access(project_id, current_user, db)

    async def event_generator():
        async for chunk in stream_document_events(db, project_id, document_id):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{document_id}/retry", response_model=DocumentResponse)
async def retry_document_processing(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentResponse:
    """Re-run ingestion for a stuck or failed document."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    schedule_process_document(
        document.id,
        project_id,
        force_full_extract=True,
    )
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/content", response_model=DocumentContentResponse)
async def get_document_content(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentContentResponse:
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.status not in PREVIEW_STATUSES:
        raise HTTPException(
            status_code=404,
            detail="Extracted content not available yet",
        )

    path = document.extracted_text_path or extracted_md_key(project_id, document_id)
    storage = get_storage_service()
    if not storage.file_exists(path):
        raise HTTPException(status_code=404, detail="Extracted content not found")

    raw = storage.download_file(path).decode("utf-8")
    truncated = len(raw) > CONTENT_MAX_CHARS
    content = raw[:CONTENT_MAX_CHARS] if truncated else raw

    return DocumentContentResponse(
        document_id=document_id,
        content=content,
        truncated=truncated,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    project_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    storage = get_storage_service()
    for path in (
        document.storage_path,
        document.extracted_text_path,
        extracted_md_key(project_id, document_id),
        extracted_meta_key(project_id, document_id),
    ):
        if path and storage.file_exists(path):
            try:
                storage.delete_file(path)
            except Exception as e:
                logger.error("Failed to delete %s: %s", path, e)

    try:
        # Stop ingest before wiping Neo4j/OpenSearch so the worker cannot race
        # delete_document_subgraph (EntityNotFound / stuck at 75%).
        cancel_document_ingest(document.id)
        cancel_document_summary(document.id)
        rag_mode, rag_config, _ = await get_project_rag_context(db, project_id)
        create_pipeline(rag_config, rag_mode=rag_mode).delete_document_data(
            str(document.id), project_id=str(project_id)
        )
    except Exception as e:
        logger.error("Failed to delete vectors: %s", e)

    await db.delete(document)
    await db.commit()
