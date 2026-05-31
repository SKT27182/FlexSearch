"""Redis pub/sub for document processing status."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.services.redis_client import get_redis
from app.utils.logger import create_logger

logger = create_logger(__name__)

DOCUMENT_CHANNEL = "flexsearch:document:{document_id}"
PROJECT_CHANNEL = "flexsearch:project:{project_id}"


def document_channel(document_id: UUID | str) -> str:
    return DOCUMENT_CHANNEL.format(document_id=document_id)


def project_channel(project_id: UUID | str) -> str:
    return PROJECT_CHANNEL.format(project_id=project_id)


async def publish_document_status(payload: dict[str, Any]) -> None:
    """Publish status to document and project channels."""
    client = await get_redis()
    if client is None:
        return
    message = json.dumps(payload, default=str)
    doc_id = payload.get("document_id")
    proj_id = payload.get("project_id")
    try:
        if doc_id:
            await client.publish(document_channel(doc_id), message)
        if proj_id:
            await client.publish(project_channel(proj_id), message)
    except Exception as exc:
        logger.warning("Failed to publish document status: %s", exc)


def status_payload_from_document(document: Any) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "project_id": str(document.project_id),
        "status": document.status.value
        if hasattr(document.status, "value")
        else str(document.status),
        "processing_step": document.processing_step,
        "progress_pct": document.progress_pct,
        "chunk_count": document.chunk_count,
        "error_message": document.error_message,
        "filename": document.filename,
    }
