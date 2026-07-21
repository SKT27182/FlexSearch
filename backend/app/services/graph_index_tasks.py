"""Debounced scheduling for project-level GraphRAG rebuilds."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.result import AsyncResult
from sqlalchemy import select

from app.db.models import Document, Project, RagMode
from app.db.postgres import async_session_maker
from app.schemas.graph_index import GraphIndexState
from app.utils.logger import create_logger

logger = create_logger(__name__)

_DEBOUNCE_SECONDS = 5.0
# Match Celery rebuild_graph_index_task time_limit (+ small buffer).
_STALE_INDEXING_SECONDS = 60 * 70
_RUNNING = frozenset({"STARTED", "RETRY", "RECEIVED"})
_TERMINAL = frozenset({"SUCCESS", "FAILURE", "REVOKED"})


def graph_rebuild_task_id(project_id: UUID) -> str:
    return f"graph_rebuild:{project_id}"


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
            indexing_started_at=prev.indexing_started_at,
            fingerprint=prev.fingerprint,
            error=error,
            document_count=prev.document_count,
            entity_count=prev.entity_count,
            passage_count=prev.passage_count,
        ).to_db()
        await db.commit()


def _parse_started_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _inspect_has_task(mapping: dict[str, Any] | None, task_id: str) -> bool:
    if not mapping:
        return False
    for worker_items in mapping.values():
        for item in worker_items or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == task_id:
                return True
            req = item.get("request") or {}
            if isinstance(req, dict) and req.get("id") == task_id:
                return True
    return False


def _any_celery_tasks_alive(task_ids: list[str], app: Any) -> bool:
    """True if any task id is still queued or running (inspect + AsyncResult).

    Detects dead STARTED/RECEIVED/RETRY tasks (worker crash) when workers
    respond to inspect but no longer own the task.
    """
    if not task_ids:
        return False

    states = {tid: AsyncResult(tid, app=app).state for tid in task_ids}
    candidates = [tid for tid, state in states.items() if state not in _TERMINAL]
    if not candidates:
        return False

    try:
        insp = app.control.inspect(timeout=1.5)
        if insp is None:
            return any(
                states[tid] in _RUNNING or states[tid] == "PENDING"
                for tid in candidates
            )

        active = insp.active()
        reserved = insp.reserved()
        scheduled = insp.scheduled()
        workers_responded = any(m is not None for m in (active, reserved, scheduled))

        for tid in candidates:
            if _inspect_has_task(active, tid):
                return True
            if _inspect_has_task(reserved, tid):
                return True
            if _inspect_has_task(scheduled, tid):
                return True

        for tid in candidates:
            state = states[tid]
            if state in _RUNNING:
                # Result backend says in-flight but no worker owns it → dead
                # only when workers answered inspect.
                if not workers_responded:
                    return True
                continue
            if state == "PENDING":
                # Countdown / queued, or never-seen id. Prefer timeout via
                # indexing_started_at when workers are up and the task is absent.
                if not workers_responded:
                    return True
                continue
        return False
    except Exception as exc:
        logger.debug("Celery inspect for task aliveness failed: %s", exc)
        return any(
            states[tid] in _RUNNING or states[tid] == "PENDING" for tid in candidates
        )


def is_graph_rebuild_alive(project_id: UUID) -> bool:
    """True if a Celery graph rebuild is still queued or running for this project.

    Detects dead STARTED tasks (worker crash) via Celery inspect so status can
    recover without an API restart.
    """
    from app.services.celery_tasks import rebuild_graph_index_task

    return _any_celery_tasks_alive(
        [graph_rebuild_task_id(project_id)],
        rebuild_graph_index_task.app,
    )


def _neo4j_ingest_task_ids(document_ids: list[UUID]) -> list[str]:
    from app.services.document_worker import ReindexMode

    task_ids: list[str] = []
    for document_id in document_ids:
        for mode in ReindexMode:
            task_ids.append(f"ingest:{document_id}:{mode.value}")
    return task_ids


async def is_neo4j_graph_indexing_alive(
    project_id: UUID,
    *,
    db: Any | None = None,
) -> bool:
    """True if any per-document Neo4j ingest Celery task is still in flight."""
    from app.services.celery_tasks import process_document_task

    async def _load_doc_ids(session: Any) -> list[UUID]:
        result = await session.execute(
            select(Document.id).where(Document.project_id == project_id)
        )
        return list(result.scalars().all())

    if db is not None:
        document_ids = await _load_doc_ids(db)
    else:
        async with async_session_maker() as session:
            document_ids = await _load_doc_ids(session)

    return _any_celery_tasks_alive(
        _neo4j_ingest_task_ids(document_ids),
        process_document_task.app,
    )


async def _graph_indexing_alive(
    project_id: UUID,
    backend: str,
    *,
    db: Any | None = None,
) -> bool:
    if backend == "microsoft":
        return is_graph_rebuild_alive(project_id)
    return await is_neo4j_graph_indexing_alive(project_id, db=db)


def _indexing_timed_out(prev: GraphIndexState) -> bool:
    started = _parse_started_at(prev.indexing_started_at)
    if started is None:
        return False
    age = (datetime.now(timezone.utc) - started).total_seconds()
    return age >= _STALE_INDEXING_SECONDS


async def reconcile_stale_graph_index(project_id: UUID) -> bool:
    """If DB says indexing but the rebuild is dead/stale, mark failed.

    Called from the status API so recovery does not require process restart.
    Returns True when the project was reconciled.
    """
    async with async_session_maker() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project or project.rag_mode != RagMode.GRAPH:
            return False
        prev = GraphIndexState.from_db(project.graph_index_status)
        if prev.status != "indexing":
            return False

        backend = prev.backend or "microsoft"
        alive = await _graph_indexing_alive(project_id, backend, db=db)

        if alive and not _indexing_timed_out(prev):
            return False

        error = (
            "Indexing timed out — click Rebuild."
            if _indexing_timed_out(prev)
            else "Indexing worker is no longer running — click Rebuild."
        )
        project.graph_index_status = GraphIndexState(
            backend=backend,
            status="failed",
            indexed_at=prev.indexed_at,
            indexing_started_at=prev.indexing_started_at,
            fingerprint=prev.fingerprint,
            error=error,
            document_count=prev.document_count,
            entity_count=prev.entity_count,
            passage_count=prev.passage_count,
        ).to_db()
        await db.commit()
        logger.warning(
            "Reconciled stale graph index for project %s (%s)",
            project_id,
            error,
        )
        return True


async def reconcile_interrupted_graph_indexes() -> int:
    """Reset GRAPH projects left at status 'indexing' when no rebuild is alive.

    Called on API startup. Live Celery builds (Microsoft rebuild or Neo4j
    ingest) are left alone; only orphaned 'indexing' rows are marked failed.
    Returns the number of projects reconciled.
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
            backend = prev.backend or "microsoft"
            if await _graph_indexing_alive(project.id, backend, db=db):
                logger.info(
                    "Startup reconcile skipped project %s — graph indexing still alive",
                    project.id,
                )
                continue
            project.graph_index_status = GraphIndexState(
                backend=backend,
                status="failed",
                indexed_at=prev.indexed_at,
                indexing_started_at=prev.indexing_started_at,
                fingerprint=prev.fingerprint,
                error="Indexing was interrupted by a server restart — click Rebuild.",
                document_count=prev.document_count,
                entity_count=prev.entity_count,
                passage_count=prev.passage_count,
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
    generation: int | None = None,
) -> str | None:
    """Schedule a debounced Celery graph index rebuild for a project.

    Uses an idempotent task id `graph_rebuild:{project_id}` and Celery
    countdown for debounce. Re-scheduling replaces a queued countdown task
    with a fresh task id (never revoke+reuse — workers discard that).
    """
    from app.services.celery_schedule import prepare_reusable_task_id
    from app.services.celery_tasks import rebuild_graph_index_task

    base_id = graph_rebuild_task_id(project_id)
    task_id = prepare_reusable_task_id(
        base_id,
        rebuild_graph_index_task.app,
        replace_queued=True,
    )
    if task_id is None:
        logger.info(
            "Graph rebuild already running for %s (task_id=%s)",
            project_id,
            base_id,
        )
        return base_id

    async_result = rebuild_graph_index_task.apply_async(
        args=[str(project_id)],
        kwargs={"generation": generation},
        task_id=task_id,
        queue="graph",
        countdown=max(0.0, float(debounce_seconds)),
    )
    logger.info(
        "Scheduled Celery graph rebuild for %s in %.0fs (task_id=%s)",
        project_id,
        debounce_seconds,
        async_result.id,
    )
    return async_result.id
