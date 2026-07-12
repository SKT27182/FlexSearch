"""Async worker entry for hierarchical summary Celery jobs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.db.models import Document, DocumentStatus, RagMode
from app.db.postgres import async_session_maker
from app.schemas.rag_config import GraphRagConfig, VectorRagConfig, parse_rag_config
from app.services.document_status import update_document_status
from app.services.document_worker import get_project_rag_context
from app.services.summary.service import build_document_summaries, summary_meta_payload
from app.utils.logger import create_logger

logger = create_logger(__name__)


async def run_document_summary_job(document_id: UUID, project_id: UUID) -> dict:
    """
    Build summaries for a vector document.

    Skips Microsoft GraphRAG projects and graph mode entirely.
    """
    async with async_session_maker() as db:
        rag_mode, rag_config, _project = await get_project_rag_context(db, project_id)

        if rag_mode == RagMode.GRAPH:
            if isinstance(rag_config, GraphRagConfig) and rag_config.graph_backend == "microsoft":
                logger.info(
                    "Skipping summaries for Microsoft GraphRAG project %s",
                    project_id,
                )
                return {"skipped": True, "reason": "microsoft_graphrag"}
            logger.info("Skipping summaries for graph project %s", project_id)
            return {"skipped": True, "reason": "graph_mode"}

        assert isinstance(rag_config, VectorRagConfig)
        if not rag_config.summaries.enabled:
            return {"skipped": True, "reason": "summaries.disabled"}

        result = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == project_id,
            )
        )
        document = result.scalar_one_or_none()
        if not document:
            logger.error("Summary job: document %s not found", document_id)
            return {"skipped": True, "reason": "document_not_found"}

        await update_document_status(
            db,
            document,
            status=document.status if document.status == DocumentStatus.COMPLETED else DocumentStatus.INDEXING,
            processing_step="Building hierarchical summaries…",
            progress_pct=92,
        )

        try:
            job = await build_document_summaries(
                project_id=str(project_id),
                document_id=str(document_id),
                filename=document.filename,
                config=rag_config.summaries,
            )
        except Exception as exc:
            logger.exception("Summary build failed for %s", document_id)
            await update_document_status(
                db,
                document,
                status=DocumentStatus.COMPLETED,
                processing_step="Summaries failed (chunks still searchable)",
                progress_pct=100,
                error_message=f"summary: {exc}",
            )
            raise

        meta = summary_meta_payload(job)
        step = (
            f"Summaries skipped ({job.reason})"
            if job.skipped
            else f"Summaries ready ({job.cluster_count} clusters)"
        )
        await update_document_status(
            db,
            document,
            status=DocumentStatus.COMPLETED,
            processing_step=step,
            progress_pct=100,
        )
        logger.info("Summary job done document=%s meta=%s", document_id, meta)
        return meta
