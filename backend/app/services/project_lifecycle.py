"""Shared project and document removal with index cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, Project
from app.services.outbox import add_outbox_event


async def delete_document_fully(
    db: AsyncSession,
    document: Document,
    *,
    project_id: UUID,
) -> None:
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


async def delete_project_fully(db: AsyncSession, project: Project) -> None:
    project.deleting_at = datetime.now(timezone.utc)
    add_outbox_event(
        db,
        event_type="cleanup_project",
        aggregate_type="project",
        aggregate_id=project.id,
        project_id=project.id,
        payload={},
    )
