"""Update document status in DB and publish to Redis."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Document, DocumentStatus
from app.services.document_events import (
    publish_document_status,
    status_payload_from_document,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)


async def update_document_status(
    db: AsyncSession,
    document: Document,
    *,
    status: DocumentStatus,
    processing_step: str | None = None,
    progress_pct: int | None = None,
    chunk_count: int | None = None,
    error_message: str | None = None,
    extracted_text_path: str | None = None,
    extraction_config_hash: str | None = None,
    extracted_at: datetime | None = None,
    clear_error: bool = False,
) -> bool:
    """Persist status. Returns False if the document row was deleted concurrently."""
    document.status = status
    if processing_step is not None:
        document.processing_step = processing_step
    if progress_pct is not None:
        document.progress_pct = progress_pct
    if chunk_count is not None:
        document.chunk_count = chunk_count
    if clear_error:
        document.error_message = None
    if error_message is not None:
        document.error_message = error_message
    if extracted_text_path is not None:
        document.extracted_text_path = extracted_text_path
    if extraction_config_hash is not None:
        document.extraction_config_hash = extraction_config_hash
    if extracted_at is not None:
        document.extracted_at = extracted_at
    if status == DocumentStatus.COMPLETED:
        document.processed_at = datetime.now(timezone.utc)

    try:
        await db.commit()
        await db.refresh(document)
    except StaleDataError:
        await db.rollback()
        logger.info(
            "Document %s missing on status update to %s (deleted concurrently)",
            document.id,
            status,
        )
        return False
    await publish_document_status(status_payload_from_document(document))
    return True


async def get_document(db: AsyncSession, document_id: UUID) -> Document | None:
    from sqlalchemy import select

    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()
