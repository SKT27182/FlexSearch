"""Schedule website crawl Celery tasks."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.services.job_events import register_job_meta_sync
from app.utils.logger import create_logger

logger = create_logger(__name__)


def schedule_website_crawl(
    project_id: UUID,
    url: str,
    *,
    max_depth: int | None = None,
    max_pages: int | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool | None = None,
    use_sitemap: bool | None = None,
    rate_limit: float | None = None,
    job_id: str | None = None,
) -> str:
    from app.services.celery_tasks import website_crawl_task

    job_id = job_id or f"crawl:{project_id}:{uuid4().hex[:12]}"
    register_job_meta_sync(job_id, project_id=project_id, job_type="crawl")
    website_crawl_task.apply_async(
        args=[job_id, str(project_id), url],
        kwargs={
            "max_depth": max_depth,
            "max_pages": max_pages,
            "exclude_patterns": exclude_patterns,
            "respect_robots": respect_robots,
            "use_sitemap": use_sitemap,
            "rate_limit": rate_limit,
        },
        task_id=job_id,
        queue="default",
    )
    logger.info(
        "Enqueued website crawl job_id=%s project=%s url=%s", job_id, project_id, url
    )
    return job_id
