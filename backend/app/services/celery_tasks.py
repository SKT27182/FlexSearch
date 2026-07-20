"""Celery task definitions for document ingest, graph, summary, crawl, bulk."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.celery_app import celery_app
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery worker process.

    ``asyncio.run`` creates a new event loop per call. Global SQLAlchemy and
    Redis clients keep connections bound to that loop, so both must be closed
    before the loop ends. Otherwise the next task can reuse a client attached
    to a closed loop.
    """

    async def _wrapper():
        try:
            return await coro
        finally:
            try:
                from app.services.redis_client import close_redis

                await close_redis()
            except Exception:
                logger.debug("close_redis() after Celery task failed", exc_info=True)
            try:
                from app.db.postgres import engine

                await engine.dispose()
            except Exception:
                logger.debug("engine.dispose() after Celery task failed", exc_info=True)

    return asyncio.run(_wrapper())


@celery_app.task(
    name="app.services.celery_tasks.process_document_task",
    bind=True,
    max_retries=2,
    soft_time_limit=60 * 30,
    time_limit=60 * 35,
)
def process_document_task(
    self,
    document_id: str,
    project_id: str,
    *,
    force_full_extract: bool = False,
    mode: str = "auto",
) -> dict:
    """Ingest / reindex a document (vector or graph extract path)."""
    from app.services.document_worker import ReindexMode, process_document

    logger.info(
        "Celery ingest start document=%s project=%s mode=%s task_id=%s",
        document_id,
        project_id,
        mode,
        self.request.id,
    )
    try:
        _run_async(
            process_document(
                UUID(document_id),
                UUID(project_id),
                force_full_extract=force_full_extract,
                mode=ReindexMode(mode),
            )
        )
    except Exception as exc:
        logger.exception(
            "Celery ingest failed document=%s: %s", document_id, exc
        )
        try:
            _run_async(
                _mark_document_ingest_failed(
                    UUID(document_id), UUID(project_id), str(exc)
                )
            )
        except Exception:
            logger.exception(
                "Could not persist FAILED status for document=%s", document_id
            )
        raise
    return {"document_id": document_id, "status": "ok"}


async def _mark_document_ingest_failed(
    document_id: UUID, project_id: UUID, error_message: str
) -> None:
    """Best-effort status update when process_document crashes before its own handler."""
    from sqlalchemy import select

    from app.db.models import Document, DocumentStatus
    from app.db.postgres import async_session_maker
    from app.services.document_status import update_document_status

    async with async_session_maker() as db:
        result = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == project_id,
            )
        )
        document = result.scalar_one_or_none()
        if document is None:
            return
        if document.status == DocumentStatus.COMPLETED:
            return
        await update_document_status(
            db,
            document,
            status=DocumentStatus.FAILED,
            processing_step="Failed",
            progress_pct=0,
            error_message=error_message[:2000],
        )


@celery_app.task(
    name="app.services.celery_tasks.rebuild_graph_index_task",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 60,
    time_limit=60 * 65,
)
def rebuild_graph_index_task(self, project_id: str) -> dict:
    """Rebuild Microsoft GraphRAG / Neo4j project graph index."""
    from app.services.graph_index_tasks import (
        _acquire_in_flight,
        _mark_graph_index_failed,
        _release_in_flight,
    )
    from app.services.graphrag_workspace import get_graphrag_workspace

    pid = UUID(project_id)
    if not _acquire_in_flight(pid):
        logger.info(
            "Graph rebuild skipped; already in flight for %s", project_id
        )
        return {"project_id": project_id, "status": "coalesced"}

    logger.info(
        "Celery graph rebuild start project=%s task_id=%s",
        project_id,
        self.request.id,
    )
    try:
        workspace = get_graphrag_workspace()
        # Task already holds _in_flight; do not re-acquire inside workspace.
        _run_async(
            workspace.build_index_for_project(
                pid, is_update=True, manage_in_flight=False
            )
        )
        return {"project_id": project_id, "status": "ok"}
    except Exception as exc:
        logger.error("Celery graph rebuild failed for %s: %s", project_id, exc)
        _run_async(_mark_graph_index_failed(pid, str(exc)))
        raise
    finally:
        _release_in_flight(pid)


@celery_app.task(
    name="app.services.celery_tasks.build_document_summaries_task",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 20,
    time_limit=60 * 25,
)
def build_document_summaries_task(
    self,
    document_id: str,
    project_id: str,
) -> dict:
    """Build hierarchical summaries for a vector document (summary queue)."""
    from app.services.summary_worker import run_document_summary_job

    logger.info(
        "Celery summary start document=%s project=%s task_id=%s",
        document_id,
        project_id,
        self.request.id,
    )
    try:
        result = _run_async(
            run_document_summary_job(UUID(document_id), UUID(project_id))
        )
        return {"document_id": document_id, "status": "ok", **(result or {})}
    except Exception as exc:
        logger.exception(
            "Celery summary failed document=%s: %s", document_id, exc
        )
        raise


@celery_app.task(
    name="app.services.celery_tasks.website_crawl_task",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 45,
    time_limit=60 * 50,
)
def website_crawl_task(
    self,
    job_id: str,
    project_id: str,
    url: str,
    *,
    max_depth: int | None = None,
    max_pages: int | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool | None = None,
    use_sitemap: bool | None = None,
    rate_limit: float | None = None,
) -> dict:
    """BFS crawl website pages into the shared ingest pipeline."""
    from app.services.website.crawl_worker import run_website_crawl_job

    logger.info(
        "Celery crawl start job=%s project=%s url=%s",
        job_id,
        project_id,
        url,
    )
    return _run_async(
        run_website_crawl_job(
            job_id,
            UUID(project_id),
            url,
            max_depth=max_depth,
            max_pages=max_pages,
            exclude_patterns=exclude_patterns,
            respect_robots=respect_robots,
            use_sitemap=use_sitemap,
            rate_limit=rate_limit,
        )
    )


@celery_app.task(
    name="app.services.celery_tasks.bulk_import_task",
    bind=True,
    max_retries=1,
    soft_time_limit=60 * 45,
    time_limit=60 * 50,
)
def bulk_import_task(
    self,
    job_id: str,
    storage_path: str,
    *,
    target_project_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict:
    """Import a .ragpack from MinIO into a project."""
    from app.services.bulk.bulk_worker import run_bulk_import_job
    from app.services.storage import get_storage_service

    logger.info("Celery bulk import start job=%s path=%s", job_id, storage_path)
    storage = get_storage_service()
    if not storage.file_exists(storage_path):
        raise FileNotFoundError(f"Import archive missing: {storage_path}")
    archive_bytes = storage.download_file(storage_path)
    return _run_async(
        run_bulk_import_job(
            job_id,
            archive_bytes=archive_bytes,
            target_project_id=(
                UUID(target_project_id) if target_project_id else None
            ),
            owner_user_id=UUID(owner_user_id) if owner_user_id else None,
        )
    )
