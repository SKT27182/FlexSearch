"""Shared project and document removal with index cleanup."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Project, RagMode
from app.schemas.project import graph_backend_for_project
from app.rag.pipeline import create_pipeline
from app.schemas.rag_config import parse_rag_config
from app.services.document_storage import extracted_md_key, extracted_meta_key
from app.services.document_worker import get_project_rag_context
from app.services.project_index_service import wipe_index_for_mode
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


async def delete_document_fully(
    db: AsyncSession,
    document: Document,
    *,
    project_id: UUID,
) -> None:
    storage = get_storage_service()
    for path in (
        document.storage_path,
        document.extracted_text_path,
        extracted_md_key(project_id, document.id),
        extracted_meta_key(project_id, document.id),
    ):
        if path and storage.file_exists(path):
            try:
                storage.delete_file(path)
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", path, exc)

    try:
        rag_mode, rag_config, _ = await get_project_rag_context(db, project_id)
        create_pipeline(rag_config, rag_mode=rag_mode).delete_document_data(
            str(document.id), project_id=str(project_id)
        )
    except Exception as exc:
        logger.warning("Failed to delete document index data: %s", exc)

    await db.delete(document)


async def delete_project_fully(db: AsyncSession, project: Project) -> None:
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    graph_backend = graph_backend_for_project(rag_mode, project.rag_config)
    try:
        wipe_index_for_mode(
            project.id,
            from_mode=rag_mode.value,
            graph_backend=graph_backend if rag_mode == RagMode.GRAPH else None,
        )
    except Exception as exc:
        logger.error("Failed to delete project RAG data: %s", exc)

    await db.delete(project)
