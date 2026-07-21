"""Schedule bulk import Celery tasks."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.services.job_events import register_job_meta_sync
from app.utils.logger import create_logger

logger = create_logger(__name__)


def schedule_bulk_import(
    *,
    storage_path: str,
    target_project_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    job_id: str | None = None,
) -> str:
    from app.services.celery_tasks import bulk_import_task

    job_id = job_id or f"bulk:{uuid4().hex[:16]}"
    if target_project_id is not None:
        register_job_meta_sync(
            job_id,
            project_id=target_project_id,
            job_type="bulk",
            owner_user_id=owner_user_id,
        )
    bulk_import_task.apply_async(
        args=[job_id, storage_path],
        kwargs={
            "target_project_id": str(target_project_id) if target_project_id else None,
            "owner_user_id": str(owner_user_id) if owner_user_id else None,
        },
        task_id=job_id,
        queue="default",
    )
    logger.info("Enqueued bulk import job_id=%s path=%s", job_id, storage_path)
    return job_id
