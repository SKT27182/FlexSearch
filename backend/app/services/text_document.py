"""Create documents from text/bytes and enqueue the shared ingest pipeline."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus
from app.services.document_status import update_document_status
from app.services.document_storage import raw_object_key
from app.services.document_tasks import schedule_process_document
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)

# MIME types accepted for crawl / bulk text paths (worker extractors handle these).
TEXT_INGEST_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }
)


def guess_content_type(
    filename: str, fallback: str = "application/octet-stream"
) -> str:
    ext = Path(filename).suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return mapping.get(ext, fallback)


async def create_and_enqueue_document(
    db: AsyncSession,
    *,
    project_id: UUID,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> Document:
    """
    Persist a Document + MinIO object and schedule Celery ingest.

    Crawl / bulk use this so pages land in the same worker path as uploads
    (extract → preprocess → chunk → index → optional summaries).
    """
    ctype = content_type or guess_content_type(filename)
    safe_name = filename.replace("/", "_").replace("\\", "_")[:200] or "untitled.md"

    document = Document(
        project_id=project_id,
        filename=safe_name,
        content_type=ctype,
        storage_path="",
        file_size=len(data),
        status=DocumentStatus.UPLOADED,
        processing_step="Upload received",
        progress_pct=10,
    )
    db.add(document)
    await db.flush()

    storage_path = raw_object_key(project_id, document.id, safe_name)
    document.storage_path = storage_path
    await db.commit()
    await db.refresh(document)

    storage = get_storage_service()
    storage.upload_file(path=storage_path, data=data, content_type=ctype)

    await update_document_status(
        db,
        document,
        status=DocumentStatus.STORED,
        processing_step="Saved to storage",
        progress_pct=25,
    )

    schedule_process_document(document.id, project_id)
    logger.info(
        "Enqueued document %s (%s) for project %s",
        document.id,
        safe_name,
        project_id,
    )
    return document
