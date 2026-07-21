"""Website crawl API."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import verify_project_access
from app.core.config import settings
from app.core.dependencies import get_current_active_user, get_db
from app.core.rate_limit import CRAWL_RULE, check_rate_limit
from app.db.models import User
from app.services.url_safety import UnsafeURLError, assert_public_url
from app.services.job_events import register_job_meta
from app.services.outbox import add_outbox_event
from app.services.website.schemas import (
    WebsiteCrawlRequest,
    WebsiteCrawlSubmitResponse,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["website"])


@router.post(
    "/crawl",
    response_model=WebsiteCrawlSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_website_crawl(
    project_id: UUID,
    body: WebsiteCrawlRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WebsiteCrawlSubmitResponse:
    """Enqueue a robots-aware BFS crawl; pages enter the shared ingest pipeline."""
    await check_rate_limit(request, CRAWL_RULE, user_id=str(current_user.id))
    await verify_project_access(project_id, current_user, db)

    url = str(body.url)
    if settings.crawl_block_private_urls:
        try:
            assert_public_url(url)
        except UnsafeURLError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsafe crawl URL: {exc}",
            ) from exc

    event_id = uuid4()
    job_id = f"crawl:{project_id}:{event_id.hex[:12]}"
    add_outbox_event(
        db,
        event_type="website_crawl",
        aggregate_type="job",
        aggregate_id=event_id,
        project_id=project_id,
        payload={
            "job_id": job_id,
            "url": url,
            "max_depth": body.max_depth,
            "max_pages": body.max_pages,
            "exclude_patterns": body.exclude_patterns,
            "respect_robots": body.respect_robots,
            "use_sitemap": body.use_sitemap,
            "rate_limit": body.rate_limit,
        },
    )
    await db.commit()
    await register_job_meta(job_id, project_id=project_id, job_type="crawl")
    return WebsiteCrawlSubmitResponse(
        job_id=job_id,
        status="queued",
        project_id=str(project_id),
    )
