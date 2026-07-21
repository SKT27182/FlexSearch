"""Celery crawl job: BFS pages → create documents → shared ingest pipeline."""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.db.postgres import async_session_maker
from app.services.job_events import publish_job_event
from app.services.text_document import create_and_enqueue_document
from app.services.website.crawler import crawl_website
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _safe_filename(title: str, url: str) -> str:
    base = (title or url).strip()[:80]
    for ch in ("/", "\\", ":", "?", "*", '"', "<", ">", "|"):
        base = base.replace(ch, "_")
    base = base.strip() or "page"
    if not base.lower().endswith(".md"):
        base = f"{base}.md"
    return base


async def run_website_crawl_job(
    job_id: str,
    project_id: UUID,
    start_url: str,
    *,
    max_depth: int | None = None,
    max_pages: int | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool | None = None,
    use_sitemap: bool | None = None,
    rate_limit: float | None = None,
) -> dict:
    depth = max_depth if max_depth is not None else settings.crawl_max_depth
    pages = max_pages if max_pages is not None else settings.crawl_max_pages
    robots = (
        respect_robots if respect_robots is not None else settings.crawl_respect_robots
    )
    sitemap = use_sitemap if use_sitemap is not None else settings.crawl_use_sitemap
    rate = rate_limit if rate_limit is not None else settings.crawl_rate_limit

    await publish_job_event(
        job_id,
        {
            "event": "progress",
            "stage": "crawling",
            "message": f"Starting crawl of {start_url}",
            "progress": 5,
            "pages_found": 0,
            "pages_processed": 0,
            "project_id": str(project_id),
        },
    )

    doc_ids: list[str] = []
    pages_processed = 0

    try:
        async for page in crawl_website(
            start_url,
            max_depth=depth,
            max_pages=pages,
            rate_limit=rate,
            respect_robots=robots,
            exclude_patterns=exclude_patterns,
            use_sitemap=sitemap,
        ):
            pages_processed += 1
            body = page.content.strip() if page.content else ""
            if not body:
                body = f"# {page.title}\n\nSource: {page.url}\n"
            # Prepend source header for provenance
            markdown = f"<!-- source_url: {page.url} -->\n# {page.title}\n\n{body}\n"
            filename = _safe_filename(page.title, page.url)

            async with async_session_maker() as db:
                document = await create_and_enqueue_document(
                    db,
                    project_id=project_id,
                    filename=filename,
                    data=markdown.encode("utf-8"),
                    content_type="text/markdown",
                )
                doc_ids.append(str(document.id))

            progress = min(10 + int(80 * pages_processed / max(pages, 1)), 90)
            await publish_job_event(
                job_id,
                {
                    "event": "page_complete",
                    "stage": "ingesting",
                    "message": f"Queued: {filename}",
                    "progress": progress,
                    "pages_found": pages_processed,
                    "pages_processed": pages_processed,
                    "document_id": str(document.id),
                    "project_id": str(project_id),
                },
            )

        result = {
            "event": "complete",
            "stage": "complete",
            "message": f"Crawl finished. {pages_processed} pages queued for ingest.",
            "progress": 100,
            "pages_found": pages_processed,
            "pages_processed": pages_processed,
            "document_ids": doc_ids,
            "project_id": str(project_id),
        }
        await publish_job_event(job_id, result)
        return result

    except Exception as exc:
        logger.exception("Website crawl failed job=%s: %s", job_id, exc)
        err = {
            "event": "error",
            "stage": "error",
            "message": str(exc),
            "progress": 0,
            "project_id": str(project_id),
        }
        await publish_job_event(job_id, err)
        raise
