"""
FlexSearch Backend - Documents API Router

Document upload and management endpoints.
"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_sse import stream_document_events, stream_project_events
from app.core.dependencies import get_current_active_user, get_db
from app.core.config import settings
from app.db.models import Document, DocumentStatus, Project, User
from app.schemas.document import (
    DocumentContentResponse,
    DocumentListResponse,
    DocumentResponse,
)
from app.services.document_storage import (
    extracted_md_key,
    raw_object_key,
)
from app.services.document_tasks import (
    schedule_process_document,
)
from app.services.project_access import user_can_access_project
from app.services.storage import get_storage_service
from app.services.upload_validation import spool_upload, validate_supported_upload
from app.services.outbox import add_outbox_event
from app.services.document_worker import ReindexMode
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
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.deleting_at.is_(None))
    )
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
    project = await verify_project_access(project_id, current_user, db)
    active_result = await db.execute(
        select(func.count(Document.id))
        .join(Project, Document.project_id == Project.id)
        .where(Project.owner_id == project.owner_id)
        .where(
            Document.status.not_in(
                {
                    DocumentStatus.COMPLETED,
                    DocumentStatus.FAILED,
                    DocumentStatus.DELETING,
                }
            )
        )
    )
    if int(active_result.scalar_one()) >= 2:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="At most two ingestion jobs may be active per user",
        )

    filename = file.filename or "untitled"
    upload_stream, file_size, prefix = await spool_upload(
        file, max_bytes=settings.direct_upload_max_bytes
    )
    try:
        content_type = validate_supported_upload(
            filename=filename,
            declared_content_type=file.content_type,
            prefix=prefix,
        )
    except Exception:
        upload_stream.close()
        raise

    document = Document(
        project_id=project_id,
        filename=filename,
        content_type=content_type,
        storage_path="",
        file_size=file_size,
        status=DocumentStatus.PENDING_STORAGE,
        processing_step="Upload received",
        progress_pct=10,
    )
    db.add(document)
    await db.flush()

    storage_path = raw_object_key(project_id, document.id, filename)
    document.storage_path = storage_path
    storage = get_storage_service()
    temporary_path = f"tmp/uploads/{project_id}/{document.id}"
    try:
        await asyncio.to_thread(
            storage.upload_stream,
            temporary_path,
            upload_stream,
            file_size,
            document.content_type,
        )
        add_outbox_event(
            db,
            event_type="finalize_upload",
            aggregate_type="document",
            aggregate_id=document.id,
            project_id=project_id,
            payload={
                "temporary_path": temporary_path,
                "final_path": storage_path,
                "generation": project.rag_generation,
            },
        )
        await db.commit()
        await db.refresh(document)
    except Exception:
        await db.rollback()
        try:
            await asyncio.to_thread(storage.delete_file, temporary_path)
        except Exception:
            logger.warning(
                "Could not remove failed temporary upload %s", temporary_path
            )
        raise
    finally:
        upload_stream.close()

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
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    await verify_project_access(project_id, current_user, db)

    async def event_generator():
        async for chunk in stream_project_events(
            db, project_id, request.is_disconnected
        ):
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
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    await verify_project_access(project_id, current_user, db)

    async def event_generator():
        async for chunk in stream_document_events(
            db, project_id, document_id, request.is_disconnected
        ):
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
        mode=ReindexMode.FULL,
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

    document.status = DocumentStatus.DELETING
    document.processing_step = "Cleanup queued"
    document.progress_pct = 0
    add_outbox_event(
        db,
        event_type="cleanup_document",
        aggregate_type="document",
        aggregate_id=document.id,
        project_id=project_id,
        payload={},
    )
    await db.commit()
