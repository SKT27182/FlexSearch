"""Schedule hierarchical summary jobs on the Celery ``summary`` queue."""

from __future__ import annotations

from uuid import UUID

from celery.result import AsyncResult

from app.services.celery_schedule import prepare_replace_task_id
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _summary_task_id(document_id: UUID) -> str:
    return f"summary:{document_id}"


def cancel_document_summary(document_id: UUID) -> None:
    """
    Revoke any in-flight / queued summary job for this document.

    Call before OpenSearch wipe on delete or reindex so a late upsert cannot
    resurrect ghost summaries, and so a subsequent schedule is not skipped.
    """
    from app.services.celery_tasks import build_document_summaries_task

    task_id = _summary_task_id(document_id)
    try:
        build_document_summaries_task.app.control.revoke(task_id, terminate=True)
        AsyncResult(task_id, app=build_document_summaries_task.app).forget()
        logger.info("Revoked summary task document=%s task_id=%s", document_id, task_id)
    except Exception:
        logger.debug("Could not revoke summary task %s", task_id, exc_info=True)


def schedule_document_summary(
    document_id: UUID,
    project_id: UUID,
    generation: int,
) -> str | None:
    """Enqueue hierarchical summary build for a vector document.

    Always uses a fresh task id. ``cancel_document_summary`` revokes the base
    id, and Celery workers blacklist revoked ids — reusing ``summary:{doc}``
    causes ``Discarding revoked task`` and missing summaries after reindex.
    """
    from uuid import uuid4

    from app.services.celery_tasks import build_document_summaries_task

    base_id = _summary_task_id(document_id)
    # Revoke any live base-id task, then enqueue under a never-revoked id.
    prepare_replace_task_id(base_id, build_document_summaries_task.app)
    task_id = f"{base_id}:{uuid4().hex[:8]}"

    async_result = build_document_summaries_task.apply_async(
        args=[str(document_id), str(project_id), generation],
        task_id=task_id,
        queue="summary",
    )
    logger.info(
        "Enqueued Celery summary document=%s task_id=%s",
        document_id,
        async_result.id,
    )
    return async_result.id
