"""Schedule document processing via Celery (Redis broker + SSE progress)."""

from __future__ import annotations

from uuid import UUID

from celery.result import AsyncResult

from app.services.celery_schedule import prepare_reusable_task_id
from app.services.document_worker import ReindexMode
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _ingest_task_id(document_id: UUID, mode: ReindexMode) -> str:
    """Idempotent Celery task id — one active ingest per document+mode."""
    return f"ingest:{document_id}:{mode.value}"


def cancel_document_ingest(document_id: UUID) -> None:
    """
    Revoke any in-flight / queued ingest job for this document.

    Must run before Neo4j/OpenSearch wipe on delete. Otherwise the worker keeps
    writing graph nodes while delete_document_subgraph removes them, which
    surfaces as Neo4j EntityNotFound and leaves the UI stuck at ~75%.
    """
    from app.services.celery_tasks import process_document_task

    app = process_document_task.app
    for mode in ReindexMode:
        task_id = _ingest_task_id(document_id, mode)
        try:
            app.control.revoke(task_id, terminate=True)
            AsyncResult(task_id, app=app).forget()
            logger.info(
                "Revoked ingest task document=%s task_id=%s",
                document_id,
                task_id,
            )
        except Exception:
            logger.debug("Could not revoke ingest task %s", task_id, exc_info=True)


def schedule_process_document(
    document_id: UUID,
    project_id: UUID,
    *,
    force_full_extract: bool = False,
    mode: ReindexMode = ReindexMode.AUTO,
    generation: int | None = None,
) -> str | None:
    """Enqueue process_document on the Celery `ingest` queue."""
    from app.services.celery_tasks import process_document_task

    base_id = _ingest_task_id(document_id, mode)
    task_id = prepare_reusable_task_id(base_id, process_document_task.app)
    if task_id is None:
        logger.info(
            "Ingest already running/queued for document=%s task_id=%s",
            document_id,
            base_id,
        )
        return base_id

    async_result = process_document_task.apply_async(
        args=[str(document_id), str(project_id)],
        kwargs={
            "force_full_extract": force_full_extract,
            "mode": mode.value,
            "generation": generation,
        },
        task_id=task_id,
        queue="ingest",
    )
    logger.info(
        "Enqueued Celery ingest document=%s task_id=%s",
        document_id,
        async_result.id,
    )
    return async_result.id
