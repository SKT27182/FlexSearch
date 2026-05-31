"""Schedule document processing on the running event loop."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.services.document_worker import ReindexMode, process_document
from app.utils.logger import create_logger

logger = create_logger(__name__)


def schedule_process_document(
    document_id: UUID,
    project_id: UUID,
    *,
    force_full_extract: bool = False,
    mode: ReindexMode = ReindexMode.AUTO,
) -> None:
    """Run process_document as a tracked asyncio task (more reliable than BackgroundTasks)."""

    async def _run() -> None:
        try:
            await process_document(
                document_id,
                project_id,
                force_full_extract=force_full_extract,
                mode=mode,
            )
        except Exception:
            logger.exception(
                "Background process_document failed for %s", document_id
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error(
            "No event loop for process_document %s; call from async context",
            document_id,
        )
        return

    loop.create_task(_run(), name=f"process_document:{document_id}")
