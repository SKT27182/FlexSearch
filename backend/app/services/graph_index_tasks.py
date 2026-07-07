"""Debounced scheduling for project-level GraphRAG rebuilds."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.models import Project, RagMode
from app.db.postgres import async_session_maker
from app.schemas.graph_index import GraphIndexState
from app.utils.logger import create_logger

logger = create_logger(__name__)

_DEBOUNCE_SECONDS = 5.0
_pending: dict[str, asyncio.Task[None]] = {}
# Project ids with a build currently in flight. Prevents two builds for the
# same project running concurrently when multiple documents finish in quick
# succession and the debounce cancel races with an already-running build.
_in_flight: set[str] = set()


async def _mark_graph_index_failed(project_id: UUID, error: str) -> None:
    """Ensure graph index status is not left stuck at indexing after a task crash."""
    async with async_session_maker() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project or project.rag_mode != RagMode.GRAPH:
            return
        prev = GraphIndexState.from_db(project.graph_index_status)
        if prev.status != "indexing":
            return
        project.graph_index_status = GraphIndexState(
            backend=prev.backend or "microsoft",
            status="failed",
            indexed_at=prev.indexed_at,
            fingerprint=prev.fingerprint,
            error=error,
            document_count=prev.document_count,
        ).to_db()
        await db.commit()


def _acquire_in_flight(project_id: UUID) -> bool:
    """Try to mark a project as actively building. Return False if already busy."""
    key = str(project_id)
    if key in _in_flight:
        return False
    _in_flight.add(key)
    return True


def _release_in_flight(project_id: UUID) -> None:
    _in_flight.discard(str(project_id))


def is_graph_index_in_flight(project_id: UUID) -> bool:
    return str(project_id) in _in_flight


async def reconcile_interrupted_graph_indexes() -> int:
    """Reset GRAPH projects left at status 'indexing' from a prior process.

    A Microsoft GraphRAG build runs in a worker thread; if the server is killed
    mid-build (e.g. uvicorn --reload), the in-flight status is never written
    back as failed and the frontend polls "indexing" forever. Called on startup
    so those projects surface a Rebuild affordance instead of an infinite
    spinner. Returns the number of projects reconciled.
    """
    reconciled = 0
    async with async_session_maker() as db:
        result = await db.execute(
            select(Project).where(Project.rag_mode == RagMode.GRAPH)
        )
        projects = result.scalars().all()
        for project in projects:
            prev = GraphIndexState.from_db(project.graph_index_status)
            if prev.status != "indexing":
                continue
            project.graph_index_status = GraphIndexState(
                backend=prev.backend or "microsoft",
                status="failed",
                indexed_at=prev.indexed_at,
                fingerprint=prev.fingerprint,
                error="Indexing was interrupted by a server restart — click Rebuild.",
                document_count=prev.document_count,
            ).to_db()
            reconciled += 1
            logger.warning(
                "Reconciled interrupted graph index for project %s (was 'indexing')",
                project.id,
            )
        if reconciled:
            await db.commit()
    logger.info(
        "Startup reconciliation: %d graph project(s) reset from 'indexing' to 'failed'",
        reconciled,
    )
    return reconciled


def schedule_graph_index_rebuild(
    project_id: UUID,
    *,
    debounce_seconds: float = _DEBOUNCE_SECONDS,
) -> None:
    """Schedule a debounced graph index rebuild for a project."""

    key = str(project_id)

    async def _run_after_delay() -> None:
        if is_graph_index_in_flight(project_id):
            logger.info(
                "Graph index rebuild coalesced for %s; a build is already running",
                project_id,
            )
            return
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
        except Exception as exc:
            logger.error("Graph index rebuild failed for %s: %s", project_id, exc)
            await _mark_graph_index_failed(project_id, str(exc))
        finally:
            _pending.pop(key, None)

    existing = _pending.get(key)
    if existing and not existing.done():
        existing.cancel()
        logger.debug("Cancelled pending graph index rebuild for %s", project_id)

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
