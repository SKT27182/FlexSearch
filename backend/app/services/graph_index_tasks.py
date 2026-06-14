"""Debounced scheduling for project-level GraphRAG rebuilds."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.utils.logger import create_logger

logger = create_logger(__name__)

_DEBOUNCE_SECONDS = 30.0
_pending: dict[str, asyncio.Task[None]] = {}


def schedule_graph_index_rebuild(
    project_id: UUID,
    *,
    debounce_seconds: float = _DEBOUNCE_SECONDS,
) -> None:
    """Schedule a debounced graph index rebuild for a project."""

    key = str(project_id)

    async def _run_after_delay() -> None:
        try:
            if debounce_seconds > 0:
                await asyncio.sleep(debounce_seconds)
            from app.services.graphrag_workspace import get_graphrag_workspace

            workspace = get_graphrag_workspace()
            is_update = True
            await workspace.build_index_for_project(project_id, is_update=is_update)
        except asyncio.CancelledError:
            logger.debug("Graph index rebuild cancelled for %s", project_id)
            raise
        except Exception:
            logger.exception("Graph index rebuild failed for %s", project_id)
        finally:
            _pending.pop(key, None)

    existing = _pending.get(key)
    if existing and not existing.done():
        existing.cancel()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No event loop to schedule graph index for %s", project_id)
        return

    _pending[key] = loop.create_task(_run_after_delay(), name=f"graph_index:{key}")
    logger.info(
        "Scheduled graph index rebuild for %s in %.0fs",
        project_id,
        debounce_seconds,
    )
